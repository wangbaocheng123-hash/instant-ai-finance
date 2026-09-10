#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONFIG="${HOME}/.config/instant-ai-beijing-control"
readonly IDENTITY="${CONFIG}/ssh_identity"
readonly KNOWN_HOSTS="${CONFIG}/known_hosts"

for path in "${IDENTITY}" "${KNOWN_HOSTS}"; do
  [[ -f "${path}" ]] || {
    printf '北京管理员连接文件缺失：%s\n' "${path}" >&2
    exit 1
  }
  [[ "$(stat -c '%u:%a' "${path}")" == "$(id -u):600" ]] || {
    printf '北京管理员连接文件权限不安全：%s\n' "${path}" >&2
    exit 1
  }
done

exec /usr/bin/ssh \
  -F /dev/null \
  -p 22 \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o HostKeyAlias=beijing-collector-control \
  -o UpdateHostKeys=no \
  -o UserKnownHostsFile="${KNOWN_HOSTS}" \
  -o GlobalKnownHostsFile=/dev/null \
  -o IdentitiesOnly=yes \
  -o IdentityAgent=none \
  -o ProxyCommand=none \
  -o ProxyJump=none \
  -i "${IDENTITY}" \
  singaporecodex@47.93.214.76 "$@"
