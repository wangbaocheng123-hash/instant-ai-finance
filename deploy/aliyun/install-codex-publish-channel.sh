#!/usr/bin/env bash
set -euo pipefail

CODEX_USER="compassdev"
REPOSITORY_ROOT="${INSTANT_AI_REPOSITORY_ROOT:-/opt/instant-ai/repository}"
SOURCE_SCRIPT="${REPOSITORY_ROOT}/deploy/aliyun/update-instant-ai.sh"
SOURCE_SUDOERS="${REPOSITORY_ROOT}/deploy/aliyun/compassdev-instant-ai-publish.sudoers"
PUBLISH_WRAPPER="/usr/local/sbin/instant-ai-publish"
SUDOERS_RULE="/etc/sudoers.d/compassdev-instant-ai-publish"
EXPECTED_ORIGIN="https://github.com/wangbaocheng123-hash/instant-ai-finance.git"
EXPECTED_RULE="compassdev ALL=(root) NOPASSWD: /usr/local/sbin/instant-ai-publish"

if [[ ${EUID} -ne 0 ]]; then
  echo "此安装器只允许 root 在目标服务器上运行。" >&2
  exit 1
fi
if ! id "${CODEX_USER}" >/dev/null 2>&1; then
  echo "Codex 系统用户不存在：${CODEX_USER}" >&2
  exit 1
fi
if [[ ! -d "${REPOSITORY_ROOT}/.git" || ! -f "${SOURCE_SCRIPT}" || ! -f "${SOURCE_SUDOERS}" ]]; then
  echo "发布器源文件或 sudoers 模板缺失。" >&2
  exit 1
fi
for command_name in git install runuser sudo visudo; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "缺少安装所需命令：${command_name}" >&2
    exit 1
  }
done

git_safe=(git -c "safe.directory=${REPOSITORY_ROOT}" -C "${REPOSITORY_ROOT}")
[[ "$("${git_safe[@]}" branch --show-current)" == "main" ]] || {
  echo "生产仓库不在 main 分支，拒绝安装。" >&2
  exit 1
}
[[ "$("${git_safe[@]}" remote get-url origin)" == "${EXPECTED_ORIGIN}" ]] || {
  echo "生产仓库 origin 不正确，拒绝安装。" >&2
  exit 1
}
[[ -z "$("${git_safe[@]}" status --porcelain)" ]] || {
  echo "生产仓库存在未提交修改，拒绝安装。" >&2
  exit 1
}
[[ "$(<"${SOURCE_SUDOERS}")" == "${EXPECTED_RULE}" ]] || {
  echo "sudoers 模板不是预期的唯一命令，拒绝安装。" >&2
  exit 1
}

staging_directory="$(mktemp -d)"
trap 'rm -rf -- "${staging_directory}"' EXIT

install -o root -g root -m 0755 "${SOURCE_SCRIPT}" "${staging_directory}/instant-ai-publish"
install -o root -g root -m 0440 "${SOURCE_SUDOERS}" "${staging_directory}/compassdev-instant-ai-publish"
visudo -cf "${staging_directory}/compassdev-instant-ai-publish"

install -o root -g root -m 0755 "${staging_directory}/instant-ai-publish" "${PUBLISH_WRAPPER}"
install -o root -g root -m 0440 "${staging_directory}/compassdev-instant-ai-publish" "${SUDOERS_RULE}"
visudo -cf "${SUDOERS_RULE}"

runuser -u "${CODEX_USER}" -- sudo -n -l "${PUBLISH_WRAPPER}" >/dev/null
echo "即时 AI 云端 Codex 受限发布通道已安装并通过精确权限检查。"
echo "只允许：${PUBLISH_WRAPPER}"
