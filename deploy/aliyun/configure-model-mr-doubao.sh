#!/usr/bin/env bash
set -euo pipefail

UPLOAD_FILE="/var/tmp/instant-ai-doubao.env.upload"
CONFIG_DIR="/etc/instant-ai"
CONFIG_FILE="${CONFIG_DIR}/model-mr-secrets.env"
DROPIN_DIR="/etc/systemd/system/instant-ai.service.d"
DROPIN_FILE="${DROPIN_DIR}/30-model-mr-doubao.conf"
REPOSITORY_ROOT="/opt/instant-ai/repository"

if [[ ${EUID} -ne 0 ]]; then
  echo "必须由 root 配置模型先生豆包语音凭据。" >&2
  exit 1
fi
if [[ ! -f "${UPLOAD_FILE}" ]]; then
  echo "缺少固定的凭据暂存文件：${UPLOAD_FILE}" >&2
  exit 1
fi
if grep -Ev '^(INSTANT_AI_DOUBAO_ASR_(API_KEY|APP_ID|ACCESS_KEY|RESOURCE_ID)=[A-Za-z0-9._:/+=-]+|[[:space:]]*)$' "${UPLOAD_FILE}" | grep -q .; then
  echo "凭据文件包含不允许的字段或字符。" >&2
  exit 1
fi
if ! grep -Eq '^INSTANT_AI_DOUBAO_ASR_API_KEY=.+$' "${UPLOAD_FILE}" \
  && ! { grep -Eq '^INSTANT_AI_DOUBAO_ASR_APP_ID=.+$' "${UPLOAD_FILE}" \
    && grep -Eq '^INSTANT_AI_DOUBAO_ASR_ACCESS_KEY=.+$' "${UPLOAD_FILE}"; }; then
  echo "必须提供 API Key，或同时提供 App ID 与 Access Key。" >&2
  exit 1
fi

install -d -o root -g root -m 0750 "${CONFIG_DIR}" "${DROPIN_DIR}"
install -o root -g root -m 0600 "${UPLOAD_FILE}" "${CONFIG_FILE}"
rm -f -- "${UPLOAD_FILE}"
install -o root -g root -m 0644 \
  "${REPOSITORY_ROOT}/deploy/aliyun/instant-ai-model-mr.conf" \
  "${DROPIN_FILE}"
systemctl daemon-reload
systemctl restart instant-ai.service
systemctl is-active --quiet instant-ai.service
echo "模型先生豆包语音凭据已写入 Git 外 root 专用配置；服务已重启。"
