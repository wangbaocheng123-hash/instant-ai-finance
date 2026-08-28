#!/usr/bin/env bash
set -euo pipefail

PUBLIC_HOST="${1:-}"
REPOSITORY_ROOT="${INSTANT_AI_REPOSITORY_ROOT:-/opt/instant-ai/repository}"
DATA_ROOT="${INSTANT_AI_DATA_ROOT:-/var/lib/instant-ai}"
SERVICE_FILE="/etc/systemd/system/instant-ai.service"
CADDY_SITE="/etc/caddy/instant-ai.caddy"
CADDY_WRAPPER="/etc/caddy/Caddyfile.instant-ai"
CADDY_DROPIN="/etc/systemd/system/caddy.service.d/instant-ai.conf"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi
if [[ ! ${PUBLIC_HOST} =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "A valid public hostname is required." >&2
  exit 1
fi
if [[ ! -d "${REPOSITORY_ROOT}/.git" ]]; then
  echo "The Instant AI Git repository is missing: ${REPOSITORY_ROOT}" >&2
  exit 1
fi

for target in "${DATA_ROOT}" "${SERVICE_FILE}" "${CADDY_SITE}" "${CADDY_WRAPPER}" "${CADDY_DROPIN}"; do
  if [[ -e "${target}" ]]; then
    echo "Target already exists; refusing to overwrite: ${target}" >&2
    exit 1
  fi
done
if getent passwd instantai >/dev/null; then
  echo "System account instantai already exists; refusing an ambiguous install." >&2
  exit 1
fi
if ! systemctl is-active --quiet caddy.service; then
  echo "The existing Caddy service is not active; refusing to alter its runtime." >&2
  exit 1
fi

EXISTING_HEALTH="$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8080/ || true)"
STAGING="$(mktemp -d)"
trap 'rm -rf -- "${STAGING}"' EXIT

sed "s/__SERVER_NAME__/${PUBLIC_HOST}/g" "${REPOSITORY_ROOT}/deploy/aliyun/caddy-instant-ai.conf" > "${STAGING}/instant-ai.caddy"
{
  echo 'import /etc/caddy/Caddyfile'
  echo "import ${STAGING}/instant-ai.caddy"
} > "${STAGING}/Caddyfile.validate"

# Read the existing systemd EnvironmentFile literally. In particular, a bcrypt
# hash can contain dollar signs and must never be evaluated as shell syntax.
while IFS= read -r environment_line; do
  [[ -z "${environment_line}" || "${environment_line}" == \#* ]] && continue
  environment_key="${environment_line%%=*}"
  environment_value="${environment_line#*=}"
  export "${environment_key}=${environment_value}"
done < /etc/time-compass/caddy.env
caddy validate --config "${STAGING}/Caddyfile.validate"

(
  cd "${REPOSITORY_ROOT}/product"
  /usr/bin/python3 -m unittest discover -s tests -v
)
useradd --system --home-dir "${DATA_ROOT}" --create-home --shell /usr/sbin/nologin instantai
install -d -o instantai -g instantai -m 0750 "${DATA_ROOT}"
install -m 0644 "${REPOSITORY_ROOT}/deploy/aliyun/instant-ai.service" "${SERVICE_FILE}"
install -m 0644 "${STAGING}/instant-ai.caddy" "${CADDY_SITE}"
{
  echo 'import /etc/caddy/Caddyfile'
  echo "import ${CADDY_SITE}"
} > "${STAGING}/Caddyfile.instant-ai"
install -m 0644 "${STAGING}/Caddyfile.instant-ai" "${CADDY_WRAPPER}"
install -d -m 0755 /etc/systemd/system/caddy.service.d
cat > "${STAGING}/instant-ai.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile.instant-ai
ExecReload=
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile.instant-ai --force
EOF
install -m 0644 "${STAGING}/instant-ai.conf" "${CADDY_DROPIN}"

caddy validate --config "${CADDY_WRAPPER}"
systemctl daemon-reload
systemctl enable --now instant-ai.service

for _ in {1..20}; do
  if curl --fail --silent http://127.0.0.1:18765/api/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18765/api/health
echo

systemctl reload caddy.service
systemctl is-active --quiet caddy.service
CURRENT_HEALTH="$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8080/ || true)"
if [[ "${EXISTING_HEALTH}" != "${CURRENT_HEALTH}" ]]; then
  echo "Existing Time Compass health changed from ${EXISTING_HEALTH} to ${CURRENT_HEALTH}." >&2
  exit 1
fi

for _ in {1..10}; do
  if curl --fail --silent --show-error "https://${PUBLIC_HOST}/api/health"; then
    echo
    echo "Instant AI is live at https://${PUBLIC_HOST}/"
    exit 0
  fi
  sleep 3
done

echo "Instant AI is running locally, but public HTTPS verification did not finish." >&2
exit 1
