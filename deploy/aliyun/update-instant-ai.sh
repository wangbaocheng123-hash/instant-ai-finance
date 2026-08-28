#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="${INSTANT_AI_REPOSITORY_ROOT:-/opt/instant-ai/repository}"
BRANCH="${INSTANT_AI_BRANCH:-main}"

if [[ ! -d "${REPOSITORY_ROOT}/.git" ]]; then
  echo "即时 AI Git 仓库不存在：${REPOSITORY_ROOT}" >&2
  exit 1
fi

cd "${REPOSITORY_ROOT}"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "云端仓库存在未提交修改，已停止更新，防止覆盖其他 Codex 的工作。" >&2
  exit 1
fi

git fetch --prune origin "${BRANCH}"
git checkout "${BRANCH}"
git pull --ff-only origin "${BRANCH}"

/usr/bin/python3 -m unittest discover -s product/tests -v
systemctl restart instant-ai.service
systemctl is-active --quiet instant-ai.service
curl --fail --silent --show-error http://127.0.0.1:18765/api/health
echo
echo "即时 AI 已更新到 $(git rev-parse --short HEAD)"
