#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONTROL_USER="singaporecodex"
readonly PUBLIC_KEY_FILE="${BEIJING_CONTROL_PUBLIC_KEY_FILE:-/opt/beijing-codex-bootstrap/singapore-control.pub}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ "$(id -u)" -eq 0 ]] || {
  printf 'install-full-admin-control: root execution required\n' >&2
  exit 1
}
[[ -s "${PUBLIC_KEY_FILE}" ]] || {
  printf 'install-full-admin-control: Singapore public key is missing\n' >&2
  exit 1
}

if ! id "${CONTROL_USER}" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "${CONTROL_USER}"
else
  usermod --shell /bin/bash "${CONTROL_USER}"
fi
passwd -l "${CONTROL_USER}" >/dev/null 2>&1 || true
install -d -o "${CONTROL_USER}" -g "${CONTROL_USER}" -m 0700 \
  "/home/${CONTROL_USER}/.ssh"
install -o "${CONTROL_USER}" -g "${CONTROL_USER}" -m 0600 \
  "${PUBLIC_KEY_FILE}" "/home/${CONTROL_USER}/.ssh/authorized_keys"

printf '%s\n' "${CONTROL_USER} ALL=(ALL:ALL) NOPASSWD: ALL" \
  >"/etc/sudoers.d/${CONTROL_USER}-cloud-admin"
chmod 0440 "/etc/sudoers.d/${CONTROL_USER}-cloud-admin"
visudo -cf "/etc/sudoers.d/${CONTROL_USER}-cloud-admin" >/dev/null

install -o root -g root -m 0755 \
  "${SCRIPT_DIR}/model-downloader-git-deploy" \
  /usr/local/sbin/model-downloader-git-deploy
install -o root -g root -m 0755 \
  "${SCRIPT_DIR}/beijing-suite-publish" \
  /usr/local/sbin/beijing-suite-publish
install -o root -g root -m 0644 \
  "${SCRIPT_DIR}/model-downloader-git-deploy.service" \
  /etc/systemd/system/model-downloader-git-deploy.service

/usr/sbin/sshd -t
systemctl daemon-reload

printf 'BEIJING_FULL_ADMIN_READY user=%s sudo=ALL\n' "${CONTROL_USER}"
printf 'BEIJING_SUITE_PUBLISHER_READY\n'
