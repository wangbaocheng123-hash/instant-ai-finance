#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ORIGINAL_REPOSITORY="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"
readonly INDEPENDENT_REPOSITORY="https://github.com/wangbaocheng123-hash/sunmao-ai-community.git"
readonly BARE_REPOSITORY="/opt/sunmao-community/production-source.git"
readonly STATE_DIR="/var/lib/sunmao-community/git-deploy"
readonly BACKUP_ROOT="/opt/sunmao-community/migration-backups"
readonly PUBLISHER_TARGET="/usr/local/sbin/sunmao-community-git-deploy"
readonly DEPLOY_SERVICE_TARGET="/etc/systemd/system/sunmao-community-git-deploy.service"
readonly APP_SERVICE_TARGET="/etc/systemd/system/sunmao-ai-community.service"
readonly PATH_UNIT_TARGET="/etc/systemd/system/sunmao-community-deploy-trigger.path"
readonly TRIGGER_FILE="/run/sunmao-community/deploy.trigger"
readonly GIT_USER="sunmaogit"

backup_dir=""
previous_origin=""
path_was_active=false
channel_changed=false
repository_moved=false
migration_complete=false

log() { printf 'sunmao-community-rejoin: %s\n' "$*"; }
fail() { log "ERROR $*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"; }

safe_remove_new_repository() {
  [[ "${BARE_REPOSITORY}" == "/opt/sunmao-community/production-source.git" ]] || fail "unsafe repository path"
  if [[ -d "${BARE_REPOSITORY}" ]]; then
    rm -rf -- "${BARE_REPOSITORY}"
  fi
}

restore_file_if_present() {
  local relative="$1" target="$2"
  [[ -f "${backup_dir}/${relative}" ]] || return 0
  install -o root -g root -m "$(stat -c '%a' "${backup_dir}/${relative}")" "${backup_dir}/${relative}" "${target}"
}

rollback() {
  local status=$?
  [[ ${status} -ne 0 && "${channel_changed}" == true && "${migration_complete}" == false ]] || exit "${status}"
  log "migration failed; restoring the previous community publication channel"
  if [[ "${repository_moved}" == true ]]; then
    safe_remove_new_repository
    if [[ -d "${backup_dir}/production-source.git" ]]; then
      mv -- "${backup_dir}/production-source.git" "${BARE_REPOSITORY}"
    fi
  fi
  for state_name in accepted-revision deployed-revision deployed-tree failed-revision; do
    if [[ -f "${backup_dir}/state/${state_name}" ]]; then
      mv -- "${backup_dir}/state/${state_name}" "${STATE_DIR}/${state_name}"
    fi
  done
  restore_file_if_present publisher "${PUBLISHER_TARGET}"
  restore_file_if_present deploy-service "${DEPLOY_SERVICE_TARGET}"
  restore_file_if_present app-service "${APP_SERVICE_TARGET}"
  restore_file_if_present path-unit "${PATH_UNIT_TARGET}"
  systemctl daemon-reload || true
  if [[ "${path_was_active}" == true ]]; then
    systemctl enable --now sunmao-community-deploy-trigger.path >/dev/null 2>&1 || true
  fi
  exit "${status}"
}
trap rollback EXIT

[[ "$(id -u)" -eq 0 ]] || fail "migration must run as root"
for command in cp curl date git id install mkdir mv rm runuser stat systemctl; do
  require_command "${command}"
done
for file in sunmao-community-git-deploy sunmao-community-git-deploy.service sunmao-ai-community.service; do
  [[ -f "${SOURCE_DIR}/${file}" ]] || fail "missing reviewed file ${file}"
done
id "${GIT_USER}" >/dev/null 2>&1 || fail "Git reader user is missing"
[[ -d "${BARE_REPOSITORY}" ]] || fail "community bare repository is missing"
systemctl is-active --quiet sunmao-ai-community.service || fail "community service must be healthy before migration"
systemctl is-active --quiet blogger-collector.service || fail "collector must remain healthy during peer migration"
systemctl is-active --quiet sunmao-community-git-deploy.service && fail "community publisher is currently running"
curl --fail --silent --show-error --max-time 4 http://127.0.0.1:18808/health >/dev/null || fail "community health check failed"

previous_origin="$(runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote get-url origin)"
case "${previous_origin}" in
  "${ORIGINAL_REPOSITORY}")
    log "original repository is already configured; refreshing reviewed units only"
    ;;
  "${INDEPENDENT_REPOSITORY}")
    backup_dir="${BACKUP_ROOT}/$(date -u +'%Y%m%dT%H%M%SZ')"
    install -d -o root -g root -m 0750 "${BACKUP_ROOT}" "${backup_dir}" "${backup_dir}/state"
    [[ ! -e "${backup_dir}/production-source.git" ]] || fail "backup target already exists"
    [[ -e "${PATH_UNIT_TARGET}" ]] && cp -a -- "${PATH_UNIT_TARGET}" "${backup_dir}/path-unit"
    [[ -e "${PUBLISHER_TARGET}" ]] && cp -a -- "${PUBLISHER_TARGET}" "${backup_dir}/publisher"
    [[ -e "${DEPLOY_SERVICE_TARGET}" ]] && cp -a -- "${DEPLOY_SERVICE_TARGET}" "${backup_dir}/deploy-service"
    [[ -e "${APP_SERVICE_TARGET}" ]] && cp -a -- "${APP_SERVICE_TARGET}" "${backup_dir}/app-service"
    systemctl is-active --quiet sunmao-community-deploy-trigger.path && path_was_active=true
    channel_changed=true
    systemctl disable --now sunmao-community-deploy-trigger.path >/dev/null 2>&1 || true
    systemctl disable --now sunmao-community-git-deploy.timer >/dev/null 2>&1 || true
    rm -f -- "${TRIGGER_FILE}"
    mv -- "${BARE_REPOSITORY}" "${backup_dir}/production-source.git"
    repository_moved=true
    for state_name in accepted-revision deployed-revision deployed-tree failed-revision; do
      if [[ -f "${STATE_DIR}/${state_name}" ]]; then
        mv -- "${STATE_DIR}/${state_name}" "${backup_dir}/state/${state_name}"
      fi
    done
    install -d -o "${GIT_USER}" -g "${GIT_USER}" -m 0750 "${BARE_REPOSITORY}"
    runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" init --bare --quiet
    runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote add origin "${ORIGINAL_REPOSITORY}"
    runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.promisor true
    runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" config remote.origin.partialclonefilter blob:none
    ;;
  *)
    fail "unexpected Git origin: ${previous_origin}"
    ;;
esac

install -o root -g root -m 0755 "${SOURCE_DIR}/sunmao-community-git-deploy" "${PUBLISHER_TARGET}"
install -o root -g root -m 0644 "${SOURCE_DIR}/sunmao-community-git-deploy.service" "${DEPLOY_SERVICE_TARGET}"
install -o root -g root -m 0644 "${SOURCE_DIR}/sunmao-ai-community.service" "${APP_SERVICE_TARGET}"
systemctl daemon-reload
systemctl enable sunmao-ai-community.service >/dev/null
systemctl disable --now sunmao-community-deploy-trigger.path >/dev/null 2>&1 || true
systemctl disable --now sunmao-community-git-deploy.timer >/dev/null 2>&1 || true

[[ "$(runuser -u "${GIT_USER}" -- git --git-dir="${BARE_REPOSITORY}" remote get-url origin)" == "${ORIGINAL_REPOSITORY}" ]] || fail "original Git origin was not installed"
systemctl is-active --quiet sunmao-ai-community.service || fail "community service changed during migration"
systemctl is-active --quiet blogger-collector.service || fail "collector changed during peer migration"
curl --fail --silent --show-error --max-time 4 http://127.0.0.1:18808/health >/dev/null || fail "community health changed during migration"

migration_complete=true
printf '%s\n' 'sunmao_community_original_repository_ready=true'
printf 'previous_origin=%s\n' "${previous_origin}"
[[ -n "${backup_dir}" ]] && printf 'recoverable_backup=%s\n' "${backup_dir}"
printf '%s\n' 'next=advance sunmao-production explicitly, then start sunmao-community-git-deploy.service once'
