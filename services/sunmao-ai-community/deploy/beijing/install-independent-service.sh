#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PUBLIC_REPOSITORY="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"
readonly BARE_REPOSITORY="/opt/sunmao-community/production-source.git"
readonly PUBLISHER_TARGET="/usr/local/sbin/sunmao-community-git-deploy"
readonly DEPLOY_SERVICE_TARGET="/etc/systemd/system/sunmao-community-git-deploy.service"
readonly APP_SERVICE_TARGET="/etc/systemd/system/sunmao-ai-community.service"
readonly GIT_USER="sunmaogit"
readonly BUILD_USER="sunmaobuild"
readonly APP_USER="sunmaocommunity"

fail() { printf 'sunmao-community-install: ERROR %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || fail "installer must run as root"
for file in sunmao-community-git-deploy sunmao-community-git-deploy.service sunmao-ai-community.service; do
  [[ -f "${SOURCE_DIR}/${file}" ]] || fail "missing reviewed file ${file}"
done
systemctl is-active --quiet blogger-collector.service || fail "collector must remain healthy during peer installation"

for user in "${GIT_USER}" "${BUILD_USER}" "${APP_USER}"; do
  if ! id "${user}" >/dev/null 2>&1; then
    useradd --system --home-dir "/var/lib/${user}" --create-home --shell /usr/sbin/nologin "${user}"
  fi
done

install -d -o root -g root -m 0755 /opt/sunmao-community /var/lib/sunmao-community/git-deploy
install -d -o "${APP_USER}" -g "${APP_USER}" -m 0750 /var/lib/sunmao-community
chown root:root /var/lib/sunmao-community/git-deploy
chmod 0755 /var/lib/sunmao-community/git-deploy

if [[ ! -d "${BARE_REPOSITORY}" ]]; then
  install -d -o "${GIT_USER}" -g "${GIT_USER}" -m 0750 "${BARE_REPOSITORY}"
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" init --bare --quiet
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote add origin "${PUBLIC_REPOSITORY}"
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.promisor true
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.partialclonefilter blob:none
fi
[[ "$(runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote get-url origin)" == "${PUBLIC_REPOSITORY}" ]] || fail "unexpected Git origin"

install -o root -g root -m 0755 "${SOURCE_DIR}/sunmao-community-git-deploy" "${PUBLISHER_TARGET}"
install -o root -g root -m 0644 "${SOURCE_DIR}/sunmao-community-git-deploy.service" "${DEPLOY_SERVICE_TARGET}"
install -o root -g root -m 0644 "${SOURCE_DIR}/sunmao-ai-community.service" "${APP_SERVICE_TARGET}"
systemctl daemon-reload
systemctl enable sunmao-ai-community.service >/dev/null
systemctl disable --now sunmao-community-git-deploy.timer >/dev/null 2>&1 || true
printf '%s\n' 'sunmao_community_independent_channel_installed=true'
printf '%s\n' 'next=push sunmao-production, start sunmao-community-git-deploy.service, then apply-caddy.sh'
