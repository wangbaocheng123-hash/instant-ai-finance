#!/usr/bin/env bash
set -euo pipefail

PUBLISH_WRAPPER="/usr/local/sbin/instant-ai-publish"
SUDOERS_RULE="/etc/sudoers.d/compassdev-instant-ai-publish"
REPOSITORY_ROOT="${INSTANT_AI_REPOSITORY_ROOT:-/opt/instant-ai/repository}"
UPDATE_SCRIPT="${REPOSITORY_ROOT}/deploy/aliyun/update-instant-ai.sh"
EXPECTED_ORIGIN="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"

fail() {
  echo "CODEX_CLOUD_PUBLISH_NOT_READY: $*" >&2
  exit 1
}

for command_name in awk git sha256sum stat sudo systemctl curl; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "缺少命令：${command_name}"
done

[[ -f "${PUBLISH_WRAPPER}" ]] || fail "发布器不存在：${PUBLISH_WRAPPER}"
[[ -f "${SUDOERS_RULE}" ]] || fail "sudoers 规则不存在：${SUDOERS_RULE}"
[[ "$(stat -c '%U:%G %a' "${PUBLISH_WRAPPER}")" == "root:root 755" ]] ||
  fail "发布器必须为 root:root 0755"
[[ "$(stat -c '%U:%G %a' "${SUDOERS_RULE}")" == "root:root 440" ]] ||
  fail "sudoers 规则必须为 root:root 0440"

sudo -n -l "${PUBLISH_WRAPPER}" >/dev/null 2>&1 ||
  fail "当前用户未获准执行精确发布器；请核对 sudo -n -l"

[[ -d "${REPOSITORY_ROOT}/.git" ]] || fail "生产 Git 仓库不存在：${REPOSITORY_ROOT}"
[[ -f "${UPDATE_SCRIPT}" ]] || fail "生产更新脚本不存在：${UPDATE_SCRIPT}"

git_safe=(git -c "safe.directory=${REPOSITORY_ROOT}" -C "${REPOSITORY_ROOT}")
[[ "$("${git_safe[@]}" branch --show-current)" == "main" ]] || fail "生产仓库不在 main 分支"
[[ "$("${git_safe[@]}" remote get-url origin)" == "${EXPECTED_ORIGIN}" ]] ||
  fail "生产仓库 origin 不是统一 GitHub 仓库"
[[ -z "$("${git_safe[@]}" status --porcelain)" ]] || fail "生产仓库存在未提交修改"

wrapper_hash="$(sha256sum "${PUBLISH_WRAPPER}" | awk '{print $1}')"
script_hash="$(sha256sum "${UPDATE_SCRIPT}" | awk '{print $1}')"
[[ "${wrapper_hash}" == "${script_hash}" ]] ||
  fail "root 发布器与当前生产更新脚本不一致；需由 root 运行安装器修复"

systemctl is-active --quiet instant-ai.service || fail "instant-ai.service 未运行"
curl --fail --silent --show-error http://127.0.0.1:18765/api/health >/dev/null ||
  fail "即时 AI 回环健康检查失败"

echo "CODEX_CLOUD_PUBLISH_READY"
echo "publisher=${PUBLISH_WRAPPER}"
echo "repository=${REPOSITORY_ROOT}"
echo "revision=$("${git_safe[@]}" rev-parse --short HEAD)"
