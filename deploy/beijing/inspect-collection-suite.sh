#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$#" -eq 0 ]] || {
  printf 'inspect-beijing-collection-suite: arguments are not accepted\n' >&2
  exit 2
}

readonly UNITS=(
  model-downloader.service
  blogger-collector.service
  blogger-collector-git-deploy.service
  blogger-collector-git-deploy.timer
)
readonly KNOWN_PATHS=(
  /var/lib/model-downloader
  /srv/model-downloader
  /srv/model-downloader/videos
  /var/lib/blogger-agent
  /opt/blogger-agent/current
  /opt/blogger-agent/production-source.git
)

declare -A checked_git_roots=()

print_git_metadata() {
  local candidate="$1"
  local git_root=""
  local revision=""
  local branch=""
  local dirty_count=""

  [[ -n "${candidate}" && -d "${candidate}" ]] || return 0
  git_root="$(git -C "${candidate}" rev-parse --show-toplevel 2>/dev/null || true)"
  [[ -n "${git_root}" ]] || return 0
  [[ -z "${checked_git_roots[${git_root}]+present}" ]] || return 0
  checked_git_roots["${git_root}"]=1

  revision="$(git -C "${git_root}" rev-parse HEAD^{commit} 2>/dev/null || true)"
  branch="$(git -C "${git_root}" branch --show-current 2>/dev/null || true)"
  dirty_count="$(git -C "${git_root}" status --porcelain=v1 --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')"
  printf 'git root=%q branch=%q revision=%q tracked_change_count=%q\n' \
    "${git_root}" "${branch}" "${revision}" "${dirty_count}"
}

for unit in "${UNITS[@]}"; do
  load_state="$(systemctl show "${unit}" --property=LoadState --value 2>/dev/null || true)"
  if [[ -z "${load_state}" || "${load_state}" == "not-found" ]]; then
    printf 'service unit=%q load_state=not-found\n' "${unit}"
    continue
  fi

  active_state="$(systemctl show "${unit}" --property=ActiveState --value)"
  sub_state="$(systemctl show "${unit}" --property=SubState --value)"
  unit_file_state="$(systemctl show "${unit}" --property=UnitFileState --value)"
  main_pid="$(systemctl show "${unit}" --property=MainPID --value)"
  service_user="$(systemctl show "${unit}" --property=User --value)"
  service_group="$(systemctl show "${unit}" --property=Group --value)"
  working_directory="$(systemctl show "${unit}" --property=WorkingDirectory --value)"
  fragment_path="$(systemctl show "${unit}" --property=FragmentPath --value)"

  printf 'service unit=%q load_state=%q active_state=%q sub_state=%q unit_file_state=%q main_pid=%q user=%q group=%q working_directory=%q fragment_path=%q\n' \
    "${unit}" "${load_state}" "${active_state}" "${sub_state}" \
    "${unit_file_state}" "${main_pid}" "${service_user}" "${service_group}" \
    "${working_directory}" "${fragment_path}"

  print_git_metadata "${working_directory}"
  if [[ "${main_pid}" =~ ^[1-9][0-9]*$ ]]; then
    process_cwd="$(readlink -e "/proc/${main_pid}/cwd" 2>/dev/null || true)"
    process_exe="$(readlink -e "/proc/${main_pid}/exe" 2>/dev/null || true)"
    printf 'process unit=%q cwd=%q executable=%q\n' \
      "${unit}" "${process_cwd}" "${process_exe}"
    print_git_metadata "${process_cwd}"
  fi
done

for path in "${KNOWN_PATHS[@]}"; do
  if [[ -e "${path}" || -L "${path}" ]]; then
    stat -c 'path=%n type=%F owner=%U group=%G mode=%a' -- "${path}"
  else
    printf 'path=%q state=missing\n' "${path}"
  fi
done

printf 'BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE\n'
