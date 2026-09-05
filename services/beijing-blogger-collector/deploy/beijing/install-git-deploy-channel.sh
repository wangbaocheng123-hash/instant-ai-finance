#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PUBLIC_REPOSITORY="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"
readonly BARE_REPOSITORY="/opt/blogger-agent/production-source.git"
readonly PUBLISHER_SOURCE="${SOURCE_DIR}/blogger-collector-git-deploy"
readonly PUBLISHER_TARGET="/usr/local/sbin/blogger-collector-git-deploy"
readonly SERVICE_SOURCE="${SOURCE_DIR}/blogger-collector-git-deploy.service"
readonly TIMER_SOURCE="${SOURCE_DIR}/blogger-collector-git-deploy.timer"
readonly SERVICE_TARGET="/etc/systemd/system/blogger-collector-git-deploy.service"
readonly TIMER_TARGET="/etc/systemd/system/blogger-collector-git-deploy.timer"
readonly GIT_USER="bloggergit"
readonly BUILD_USER="bloggerbuild"

log() {
  printf 'install-blogger-collector-git-channel: %s\n' "$*"
}

fail() {
  log "ERROR $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

[[ "$(id -u)" -eq 0 ]] || fail "installer must run as root"
for required in awk git id install mktemp rm runuser sha256sum stat systemctl useradd; do
  require_command "${required}"
done
for source in "${PUBLISHER_SOURCE}" "${SERVICE_SOURCE}" "${TIMER_SOURCE}"; do
  [[ -f "${source}" ]] || fail "reviewed channel file is missing"
done
systemctl is-active --quiet blogger-collector.service || fail "existing collector service is not healthy enough to migrate"

if ! id "${GIT_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/blogger-agent/git-reader --create-home --shell /usr/sbin/nologin "${GIT_USER}"
fi
if ! id "${BUILD_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/blogger-agent/build --create-home --shell /usr/sbin/nologin "${BUILD_USER}"
fi

install -d -o root -g root -m 0755 /opt/blogger-agent /var/lib/blogger-agent/git-deploy
if [[ ! -d "${BARE_REPOSITORY}" ]]; then
  install -d -o "${GIT_USER}" -g "${GIT_USER}" -m 0750 "${BARE_REPOSITORY}"
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" init --bare --quiet
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote add origin "${PUBLIC_REPOSITORY}"
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.promisor true
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.partialclonefilter blob:none
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config http.version HTTP/1.1
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config http.lowSpeedLimit 1024
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config http.lowSpeedTime 30
  runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" fetch \
    --filter=blob:none --depth=1 --no-tags origin \
    "+refs/heads/main:refs/remotes/origin/main"
fi
[[ "$(runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote get-url origin)" == "${PUBLIC_REPOSITORY}" ]] || fail "existing production source has an unexpected origin"

staging="$(mktemp -d)"
trap 'rm -rf -- "${staging}"' EXIT
install -o root -g root -m 0755 "${PUBLISHER_SOURCE}" "${staging}/blogger-collector-git-deploy"
install -o root -g root -m 0644 "${SERVICE_SOURCE}" "${staging}/blogger-collector-git-deploy.service"
install -o root -g root -m 0644 "${TIMER_SOURCE}" "${staging}/blogger-collector-git-deploy.timer"

install -o root -g root -m 0755 "${staging}/blogger-collector-git-deploy" "${PUBLISHER_TARGET}"
install -o root -g root -m 0644 "${staging}/blogger-collector-git-deploy.service" "${SERVICE_TARGET}"
install -o root -g root -m 0644 "${staging}/blogger-collector-git-deploy.timer" "${TIMER_TARGET}"

[[ "$(stat -c '%U:%G:%a' "${PUBLISHER_TARGET}")" == "root:root:755" ]] || fail "publisher mode check failed"
printf 'publisher_sha256=%s\n' "$(sha256sum "${PUBLISHER_TARGET}" | awk '{print $1}')" \
  > /var/lib/blogger-agent/git-deploy/infrastructure-manifest
chown root:root /var/lib/blogger-agent/git-deploy/infrastructure-manifest
chmod 0644 /var/lib/blogger-agent/git-deploy/infrastructure-manifest

systemctl daemon-reload
systemctl enable --now blogger-collector-git-deploy.timer >/dev/null
log "installed fixed root publisher and 90-second production branch timer"
