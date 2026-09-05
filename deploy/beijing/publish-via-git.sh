#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
readonly EXPECTED_REPOSITORY="wangbaocheng123-hash/instant-ai-finance.git"
readonly VERSION_URL="https://collector.amuyeye.com/health/version"

fail() {
  printf 'BEIJING_GIT_PUBLISH_FAILED: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 1 ]] || fail "只接受一个完整 Git 提交 SHA"
target="$1"
[[ "${target}" =~ ^[0-9a-f]{40}$ ]] || fail "提交必须是 40 位小写十六进制 SHA"

cd "${REPOSITORY_ROOT}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "工作区存在未提交修改"
origin_url="$(git remote get-url origin)"
[[ "${origin_url}" == *"${EXPECTED_REPOSITORY}" ]] || fail "origin 不是统一公共仓库"

git fetch --no-tags origin main
git cat-file -e "${target}^{commit}" 2>/dev/null || fail "目标提交不存在"
remote_main="$(git rev-parse refs/remotes/origin/main^{commit})"
[[ "$(git rev-parse HEAD^{commit})" == "${remote_main}" ]] || fail "本地 main 与 origin/main 不一致"
git merge-base --is-ancestor "${target}" "${remote_main}" || fail "目标提交不在 origin/main"

current_production=""
if git ls-remote --exit-code --heads origin beijing-production >/dev/null 2>&1; then
  git fetch --no-tags origin beijing-production
  current_production="$(git rev-parse refs/remotes/origin/beijing-production^{commit})"
  git merge-base --is-ancestor "${current_production}" "${target}" || fail "生产分支不能安全快进到目标提交"
fi

git push origin "${target}:refs/heads/beijing-production"

for attempt in $(seq 1 30); do
  payload="$(curl --fail --silent --show-error --max-time 10 "${VERSION_URL}" 2>/dev/null || true)"
  deployed="$(printf '%s' "${payload}" | python3 -c 'import json,sys; value=json.load(sys.stdin); required={"service","status","version","repository_revision","deployed_time"}; assert set(value)==required and value["status"]=="ok"; print(value["repository_revision"])' 2>/dev/null || true)"
  if [[ "${deployed}" == "${target}" ]]; then
    printf 'BEIJING_GIT_PUBLISH_OK\n'
    printf 'previous=%s\n' "${current_production:-none}"
    printf 'deployed=%s\n' "${target}"
    exit 0
  fi
  sleep 10
done

fail "生产分支已更新，但北京版本接口在 5 分钟内未确认目标提交 ${target}"
