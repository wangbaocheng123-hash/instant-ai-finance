#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

readonly STAGING_FILE='/var/tmp/instant-ai-blogger-mcp.env.upload'
readonly TARGET_DIR='/etc/instant-ai'
readonly TARGET_FILE="${TARGET_DIR}/blogger-mcp.env"

[[ "$(id -u)" -eq 0 ]] || {
  printf '%s\n' 'blogger_mcp_config_error=must_run_as_root' >&2
  exit 1
}

[[ -f "${STAGING_FILE}" && ! -L "${STAGING_FILE}" ]] || {
  printf '%s\n' 'blogger_mcp_config_error=staging_file_missing' >&2
  exit 1
}

TEMP_FILE=''
TOKEN_VALUE=''
CONFIG_LINES=()
trap 'rm -f -- "${TEMP_FILE:-}" "${STAGING_FILE}"; unset TOKEN_VALUE CONFIG_LINES' EXIT
mapfile -t CONFIG_LINES < "${STAGING_FILE}"
[[ "${#CONFIG_LINES[@]}" -eq 1 ]] || {
  printf '%s\n' 'blogger_mcp_config_error=invalid_staging_format' >&2
  exit 1
}

TOKEN_VALUE="${CONFIG_LINES[0]#INSTANT_AI_BLOGGER_MCP_TOKEN=}"
[[ "${CONFIG_LINES[0]}" == INSTANT_AI_BLOGGER_MCP_TOKEN=* ]] || {
  printf '%s\n' 'blogger_mcp_config_error=invalid_staging_format' >&2
  exit 1
}
[[ "${TOKEN_VALUE}" =~ ^[0-9a-f]{64}$ ]] || {
  printf '%s\n' 'blogger_mcp_config_error=invalid_token_format' >&2
  exit 1
}

install -d -o root -g root -m 0700 "${TARGET_DIR}"
TEMP_FILE="$(mktemp "${TARGET_DIR}/.blogger-mcp.env.XXXXXX")"
printf 'INSTANT_AI_BLOGGER_MCP_TOKEN=%s\n' "${TOKEN_VALUE}" > "${TEMP_FILE}"
chown root:root "${TEMP_FILE}"
chmod 0600 "${TEMP_FILE}"
mv -f -- "${TEMP_FILE}" "${TARGET_FILE}"
TEMP_FILE=''
rm -f -- "${STAGING_FILE}"
unset TOKEN_VALUE CONFIG_LINES

systemctl daemon-reload
systemctl restart instant-ai.service
systemctl is-active --quiet instant-ai.service
printf '%s\n' 'blogger_mcp_configured=true'
