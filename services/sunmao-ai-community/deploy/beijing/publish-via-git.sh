#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
readonly EXPECTED_REPOSITORY="wangbaocheng123-hash/instant-ai-finance.git"
readonly VERSION_URL="https://xn--4pv19va7008a.com/health/version"

fail() { printf 'SUNMAO_GIT_PUBLISH_FAILED: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || fail "只接受一个完整 Git 提交 SHA"
target="$1"
[[ "${target}" =~ ^[0-9a-f]{40}$ ]] || fail "提交必须是40位小写十六进制SHA"

cd "${REPOSITORY_ROOT}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "工作区存在未提交修改"
[[ "$(git remote get-url origin)" == *"${EXPECTED_REPOSITORY}" ]] || fail "origin不是统一公共仓库"
git fetch --no-tags origin "+refs/heads/main:refs/remotes/origin/main"
git cat-file -e "${target}^{commit}" 2>/dev/null || fail "目标提交不存在"
remote_main="$(git rev-parse refs/remotes/origin/main^{commit})"
[[ "$(git rev-parse HEAD^{commit})" == "${remote_main}" ]] || fail "本地与origin/main不一致"
git merge-base --is-ancestor "${target}" "${remote_main}" || fail "目标提交不在origin/main"

current_production=""
if git ls-remote --exit-code --heads origin sunmao-production >/dev/null 2>&1; then
  git fetch --no-tags origin "+refs/heads/sunmao-production:refs/remotes/origin/sunmao-production"
  current_production="$(git rev-parse refs/remotes/origin/sunmao-production^{commit})"
  git merge-base --is-ancestor "${current_production}" "${target}" || fail "社区生产分支不能安全快进"
fi

git push origin "${target}:refs/heads/sunmao-production"
printf '%s\n' 'SUNMAO_ON_DEMAND_TRIGGER_REQUIRED'
printf '%s\n' 'remote_command=systemctl start --no-block sunmao-community-git-deploy.service'

for _attempt in $(seq 1 90); do
  payload="$(curl --fail --silent --show-error --max-time 10 "${VERSION_URL}" 2>/dev/null || true)"
  deployed="$(printf '%s' "${payload}" | python3 -c 'import json,sys; value=json.load(sys.stdin); print(value.get("repository_revision", ""))' 2>/dev/null || true)"
  if [[ "${deployed}" == "${target}" ]]; then
    printf '%s\n' 'SUNMAO_GIT_PUBLISH_OK'
    printf 'previous=%s\n' "${current_production:-none}"
    printf 'deployed=%s\n' "${target}"
    exit 0
  fi
  sleep 10
done
fail "社区生产分支已更新，但公网版本在15分钟内未确认 ${target}"
