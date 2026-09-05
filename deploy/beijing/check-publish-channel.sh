#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
readonly EXPECTED_REPOSITORY="wangbaocheng123-hash/instant-ai-finance.git"
readonly VERSION_URL="https://collector.amuyeye.com/health/version"

fail() {
  printf 'BEIJING_CHANNEL_NOT_READY: %s\n' "$*" >&2
  exit 1
}

fetch_version_payload() {
  local payload
  payload="$(curl --fail --silent --show-error --max-time 10 "${VERSION_URL}" 2>/dev/null || true)"
  if [[ -n "${payload}" ]]; then
    printf '%s' "${payload}"
    return
  fi
  if command -v node >/dev/null 2>&1; then
    node -e '
      fetch(process.argv[1], {signal: AbortSignal.timeout(10000)})
        .then(async response => {
          if (!response.ok) process.exit(1);
          process.stdout.write(await response.text());
        })
        .catch(() => process.exit(1));
    ' "${VERSION_URL}" 2>/dev/null || true
  fi
}

cd "${REPOSITORY_ROOT}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "工作区存在未提交修改"
origin_url="$(git remote get-url origin)"
[[ "${origin_url}" == *"${EXPECTED_REPOSITORY}" ]] || fail "origin 不是统一公共仓库"

git fetch --no-tags origin main beijing-production
local_main="$(git rev-parse HEAD^{commit})"
remote_main="$(git rev-parse refs/remotes/origin/main^{commit})"
[[ "${local_main}" == "${remote_main}" ]] || fail "main 与 origin/main 不一致"

production="$(git rev-parse refs/remotes/origin/beijing-production^{commit})"
git merge-base --is-ancestor "${production}" "${remote_main}" || fail "生产分支不能安全快进到 main"
payload="$(fetch_version_payload)"
[[ -n "${payload}" ]] || fail "北京公网版本接口不可用"
deployed="$(printf '%s' "${payload}" | python3 -c 'import json,sys; value=json.load(sys.stdin); required={"service","status","version","repository_revision","deployed_time"}; assert set(value)==required and value["service"]=="blogger-collector" and value["status"]=="ok"; print(value["repository_revision"])' 2>/dev/null)" || fail "版本接口内容不符合安全契约"

printf 'BEIJING_CHANNEL_READY\n'
printf 'main=%s\n' "${remote_main}"
printf 'beijing_production=%s\n' "${production}"
printf 'beijing_deployed=%s\n' "${deployed}"
