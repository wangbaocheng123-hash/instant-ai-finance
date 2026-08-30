#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${INSTANT_AI_REPOSITORY_ROOT:-/opt/instant-ai/repository}"
AUTH_FILE="${INSTANT_AI_AUTH_FILE:-/var/lib/instant-ai/auth.json}"
GENERATE_PASSWORD=0
if [[ ${1:-} == "--generate" ]]; then
  GENERATE_PASSWORD=1
  shift
fi
OWNER_NAME="${1:-owner}"
DROPIN_DIR="/etc/systemd/system/instant-ai.service.d"
DROPIN_FILE="${DROPIN_DIR}/owner-auth.conf"

if [[ ${EUID} -ne 0 ]]; then
  echo "请在 Alibaba Cloud Client 的服务器终端中以 root 运行。" >&2
  exit 1
fi
if [[ ! -d "${REPOSITORY_ROOT}/.git" ]]; then
  echo "即时 AI Git 仓库不存在：${REPOSITORY_ROOT}" >&2
  exit 1
fi
if ! getent passwd instantai >/dev/null; then
  echo "instantai 服务账户不存在。" >&2
  exit 1
fi

install -d -m 0755 "${DROPIN_DIR}"
install -m 0644 "${REPOSITORY_ROOT}/deploy/aliyun/instant-ai-auth.conf" "${DROPIN_FILE}"

AUTH_COMMAND=(/usr/bin/python3 -m instant_ai.auth set-owner --username "${OWNER_NAME}" --path "${AUTH_FILE}")
if [[ ${GENERATE_PASSWORD} -eq 1 ]]; then
  AUTH_COMMAND+=(--generate-password)
fi
PYTHONPATH="${REPOSITORY_ROOT}/product" \
INSTANT_AI_LIBRARY_ROOT="/var/lib/instant-ai" \
INSTANT_AI_AUTH_FILE="${AUTH_FILE}" \
"${AUTH_COMMAND[@]}"

chown instantai:instantai "${AUTH_FILE}"
chmod 0600 "${AUTH_FILE}"
systemctl daemon-reload
systemctl restart instant-ai.service
systemctl is-active --quiet instant-ai.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:18765/api/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18765/api/health
echo
echo "即时 AI 主人账户已启用；密码未写入 Git 或终端历史。"
