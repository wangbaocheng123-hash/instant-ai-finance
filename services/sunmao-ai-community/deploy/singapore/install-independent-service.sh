#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SHARED_SOURCE_DIR="${SOURCE_DIR}/../beijing"
readonly PUBLIC_REPOSITORY="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"
readonly BARE_REPOSITORY="/opt/sunmao-community/production-source.git"
readonly PUBLISHER_TARGET="/usr/local/sbin/sunmao-community-git-deploy"
readonly DEPLOY_SERVICE_TARGET="/etc/systemd/system/sunmao-community-git-deploy.service"
readonly APP_SERVICE_TARGET="/etc/systemd/system/sunmao-ai-community.service"
readonly SUDOERS_TARGET="/etc/sudoers.d/compassdev-sunmao-community-publish"
readonly GIT_USER="sunmaogit"
readonly BUILD_USER="sunmaobuild"
readonly APP_USER="sunmaocommunity"

fail() { printf 'sunmao-community-singapore-install: ERROR %s\n' "$*" >&2; exit 1; }
require_active() { systemctl is-active --quiet "$1" || fail "$2"; }

[[ "$(id -u)" -eq 0 ]] || fail "installer must run as root"
for command in curl git grep id install runuser ss systemctl useradd visudo; do
  command -v "${command}" >/dev/null 2>&1 || fail "missing command ${command}"
done
for file in sunmao-community-git-deploy sunmao-community-git-deploy.service sunmao-ai-community.service; do
  [[ -f "${SHARED_SOURCE_DIR}/${file}" ]] || fail "missing reviewed file ${file}"
done
[[ -f "${SOURCE_DIR}/compassdev-sunmao-community-publish.sudoers" ]] || fail "missing reviewed sudoers rule"

# These peer services must survive installation. This script never restarts either one.
require_active instant-ai.service "Instant AI must remain healthy"
require_active time-compass.service "Time Compass must remain healthy"
curl --fail --silent --show-error --max-time 5 https://grandpaamu.com/api/health >/dev/null || fail "Instant AI public health failed"
curl --fail --silent --show-error --max-time 5 https://mcp.amuyeye.com/health >/dev/null || fail "MCP public health failed"

if ss -H -ltn 'sport = :18808' | grep -q . && ! systemctl is-active --quiet sunmao-ai-community.service; then
  fail "port 18808 is already owned by an unknown process"
fi

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

install -o root -g root -m 0755 "${SHARED_SOURCE_DIR}/sunmao-community-git-deploy" "${PUBLISHER_TARGET}"
install -o root -g root -m 0644 "${SHARED_SOURCE_DIR}/sunmao-community-git-deploy.service" "${DEPLOY_SERVICE_TARGET}"
install -o root -g root -m 0644 "${SHARED_SOURCE_DIR}/sunmao-ai-community.service" "${APP_SERVICE_TARGET}"
install -o root -g root -m 0440 "${SOURCE_DIR}/compassdev-sunmao-community-publish.sudoers" "${SUDOERS_TARGET}"
visudo -cf "${SUDOERS_TARGET}" >/dev/null || fail "sudoers validation failed"

systemctl daemon-reload
systemctl enable sunmao-ai-community.service >/dev/null
systemctl disable --now sunmao-community-git-deploy.timer >/dev/null 2>&1 || true

require_active instant-ai.service "Instant AI changed during installation"
require_active time-compass.service "Time Compass changed during installation"
curl --fail --silent --show-error --max-time 5 https://mcp.amuyeye.com/health >/dev/null || fail "MCP changed during installation"
printf '%s\n' 'sunmao_community_singapore_channel_installed=true'
printf '%s\n' 'trigger=sudo /usr/local/sbin/sunmao-community-git-deploy'
