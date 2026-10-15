#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TEMPLATE="${SOURCE_DIR}/sunmao-community.Caddyfile.example"
readonly SITE_TARGET="/etc/caddy/sunmao-community.caddy"
readonly WRAPPER_TARGET="/etc/caddy/Caddyfile.instant-ai"
readonly TIME_COMPASS_ENV="/etc/time-compass/caddy.env"
readonly BACKUP_DIR="/etc/caddy/backups"

fail() { printf 'sunmao-community-singapore-caddy: ERROR %s\n' "$*" >&2; exit 1; }
require_url() { curl --fail --silent --show-error --max-time 10 "$1" >/dev/null || fail "$2"; }

[[ "$(id -u)" -eq 0 ]] || fail "installer must run as root"
for command in caddy curl grep install mktemp mv python3 systemctl; do
  command -v "${command}" >/dev/null 2>&1 || fail "missing command ${command}"
done
[[ -f "${TEMPLATE}" ]] || fail "community Caddy template is missing"
[[ -f "${WRAPPER_TARGET}" ]] || fail "Instant AI Caddy wrapper is missing"
[[ -f "${TIME_COMPASS_ENV}" ]] || fail "Time Compass Caddy environment is missing"

# The community owns only two exact hosts. Refuse a template that could capture MCP.
grep -Fq 'amuyeye.com, xn--4pv19va7008a.com {' "${TEMPLATE}" || fail "exact community hosts are missing"
! grep -Eq '(^|[[:space:],])\*\.|mcp\.amuyeye\.com|www\.amuyeye\.com' "${TEMPLATE}" || fail "forbidden hostname in community template"

systemctl is-active --quiet caddy.service || fail "Caddy is not active"
systemctl is-active --quiet instant-ai.service || fail "Instant AI is not active"
systemctl is-active --quiet time-compass.service || fail "Time Compass is not active"
require_url http://127.0.0.1:18808/health "community loopback health failed"
require_url https://grandpaamu.com/api/health "Instant AI public health failed"
require_url https://mcp.amuyeye.com/health "MCP public health failed"
compass_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 https://compass.grandpaamu.com/)"
[[ "${compass_status}" == 200 || "${compass_status}" == 303 ]] || fail "Time Compass public status ${compass_status}"

# Read the existing environment literally. Bcrypt values can contain dollar signs
# and must never be evaluated as shell code or printed.
while IFS= read -r environment_line; do
  [[ -z "${environment_line}" || "${environment_line}" == \#* ]] && continue
  environment_key="${environment_line%%=*}"
  environment_value="${environment_line#*=}"
  [[ "${environment_key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "invalid Caddy environment key"
  export "${environment_key}=${environment_value}"
done < "${TIME_COMPASS_ENV}"

staging="$(mktemp -d /etc/caddy/.sunmao-community.XXXXXX)"
readonly staging
readonly staged_site="${staging}/sunmao-community.caddy"
readonly staged_wrapper="${staging}/Caddyfile.instant-ai"
readonly final_wrapper="${staging}/Caddyfile.instant-ai.final"
readonly stamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly wrapper_backup="${BACKUP_DIR}/Caddyfile.instant-ai.${stamp}"
readonly site_backup="${BACKUP_DIR}/sunmao-community.caddy.${stamp}"
site_existed=false
installed=false

restore_previous_config() {
  [[ "${installed}" == true ]] || return 0
  install -o root -g root -m 0644 "${wrapper_backup}" "${WRAPPER_TARGET}"
  if [[ "${site_existed}" == true ]]; then
    install -o root -g root -m 0644 "${site_backup}" "${SITE_TARGET}"
  else
    rm -f -- "${SITE_TARGET}"
  fi
  caddy validate --config "${WRAPPER_TARGET}" >/dev/null
  systemctl reload caddy.service
}
cleanup() { rm -rf -- "${staging}"; }
on_exit() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 ]]; then
    set +e
    restore_previous_config
  fi
  cleanup
  exit "${status}"
}
trap on_exit EXIT

install -o root -g root -m 0644 "${TEMPLATE}" "${staged_site}"
caddy fmt --overwrite "${staged_site}" >/dev/null

# Validate a temporary combined configuration before writing any live file. If
# Time Compass still claims amuyeye.com this validation fails without mutation.
python3 - "${WRAPPER_TARGET}" "${staged_site}" "${staged_wrapper}" <<'PY'
from pathlib import Path
import sys

wrapper_path, site_path, output_path = map(Path, sys.argv[1:])
lines = wrapper_path.read_text(encoding="utf-8").splitlines()
lines = [line for line in lines if line.strip() != "import /etc/caddy/sunmao-community.caddy"]
lines.append(f"import {site_path}")
output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
caddy validate --config "${staged_wrapper}" >/dev/null

install -d -o root -g root -m 0755 "${BACKUP_DIR}"
install -o root -g root -m 0644 "${WRAPPER_TARGET}" "${wrapper_backup}"
if [[ -f "${SITE_TARGET}" ]]; then
  site_existed=true
  install -o root -g root -m 0644 "${SITE_TARGET}" "${site_backup}"
fi
install -o root -g root -m 0644 "${staged_site}" "${SITE_TARGET}"
python3 - "${WRAPPER_TARGET}" "${final_wrapper}" <<'PY'
from pathlib import Path
import sys

wrapper_path, output_path = map(Path, sys.argv[1:])
lines = wrapper_path.read_text(encoding="utf-8").splitlines()
marker = "import /etc/caddy/sunmao-community.caddy"
lines = [line for line in lines if line.strip() != marker]
lines.append(marker)
output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
install -o root -g root -m 0644 "${final_wrapper}" "${WRAPPER_TARGET}"
installed=true

caddy validate --config "${WRAPPER_TARGET}" >/dev/null
systemctl reload caddy.service
systemctl is-active --quiet caddy.service || fail "Caddy stopped after reload"

for host in amuyeye.com xn--4pv19va7008a.com; do
  public_health="$(curl --fail --silent --show-error --max-time 15 "https://${host}/health/version")"
  [[ "$(printf '%s' "${public_health}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("service", ""))')" == sunmao-ai-community ]] || fail "${host} is not the community"
done
require_url https://grandpaamu.com/api/health "Instant AI changed after reload"
require_url https://mcp.amuyeye.com/health "MCP changed after reload"
mcp_headers="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 10 https://mcp.amuyeye.com/mcp)"
printf '%s' "${mcp_headers}" | grep -Eq '^HTTP/[^ ]+ 401([[:space:]]|$)' || fail "MCP auth status changed"
printf '%s' "${mcp_headers}" | grep -Eiq '^WWW-Authenticate:.*resource_metadata' || fail "MCP OAuth challenge changed"
compass_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 https://compass.grandpaamu.com/)"
[[ "${compass_status}" == 200 || "${compass_status}" == 303 ]] || fail "Time Compass changed after reload"

printf '%s\n' 'sunmao_community_singapore_caddy_applied=true'
printf 'sunmao_community_caddy_wrapper_backup=%s\n' "${wrapper_backup}"
