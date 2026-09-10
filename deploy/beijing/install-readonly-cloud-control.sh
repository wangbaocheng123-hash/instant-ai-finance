#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CONTROL_USER="beijingcodex"
readonly CONTROL_HOME="/var/lib/beijing-codex-control"
readonly INSPECT_TARGET="/usr/local/sbin/beijing-suite-inspect"
readonly TRIGGER_TARGET="/usr/local/sbin/beijing-collector-publish-trigger"
readonly SUDOERS_TARGET="/etc/sudoers.d/beijing-codex-control"

fail() {
  printf 'install-readonly-cloud-control: ERROR %s\n' "$*" >&2
  exit 1
}

[[ "$#" -eq 0 ]] || fail "arguments are not accepted"
[[ "$(id -u)" -eq 0 ]] || fail "root execution required"
for command in chmod id install mktemp passwd rm systemctl truncate useradd visudo; do
  command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
[[ -f "${SOURCE_DIR}/inspect-collection-suite.sh" ]] || fail "inventory source is missing"
[[ -f "${SOURCE_DIR}/trigger-blogger-collector-publish.sh" ]] || fail "trigger source is missing"
systemctl cat blogger-collector-git-deploy.service >/dev/null 2>&1 || fail "existing fixed publisher service is missing"

if ! id "${CONTROL_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${CONTROL_HOME}" --create-home --shell /bin/bash "${CONTROL_USER}"
  passwd --lock "${CONTROL_USER}" >/dev/null
fi

install -o root -g root -m 0755 "${SOURCE_DIR}/inspect-collection-suite.sh" "${INSPECT_TARGET}"
install -o root -g root -m 0755 "${SOURCE_DIR}/trigger-blogger-collector-publish.sh" "${TRIGGER_TARGET}"

sudoers_candidate="$(mktemp)"
trap 'rm -f -- "${sudoers_candidate}"' EXIT
printf 'Defaults:%s !requiretty\n%s ALL=(root) NOPASSWD: %s, %s\n' \
  "${CONTROL_USER}" "${CONTROL_USER}" "${INSPECT_TARGET}" "${TRIGGER_TARGET}" \
  >"${sudoers_candidate}"
chmod 0600 "${sudoers_candidate}"
visudo -cf "${sudoers_candidate}" >/dev/null
install -o root -g root -m 0440 "${sudoers_candidate}" "${SUDOERS_TARGET}"
rm -f -- "${sudoers_candidate}"
trap - EXIT

printf 'BEIJING_READONLY_CLOUD_CONTROL_READY user=%s\n' "${CONTROL_USER}"
