#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${1:-/var/tmp/instant-ai-model-mr-upload}"
TARGET_ROOT="/var/lib/instant-ai/model-mr"
BACKUP_ROOT="/var/lib/instant-ai/model-mr-backups"

if [[ ${EUID} -ne 0 ]]; then
  echo "必须由 root 激活模型先生主人资料库。" >&2
  exit 1
fi
if [[ "${SOURCE_ROOT}" != "/var/tmp/instant-ai-model-mr-upload" ]]; then
  echo "只接受固定上传暂存目录：/var/tmp/instant-ai-model-mr-upload" >&2
  exit 1
fi
for required in "${SOURCE_ROOT}/public-snapshot.json" "${SOURCE_ROOT}/details" "${SOURCE_ROOT}/media"; do
  [[ -e "${required}" ]] || {
    echo "上传资料不完整：${required}" >&2
    exit 1
  }
done
if [[ -e "${TARGET_ROOT}/media" || -e "${TARGET_ROOT}/details" ]]; then
  echo "目标已经含媒体或详情；首次激活脚本拒绝覆盖。" >&2
  exit 1
fi

health="$(curl --fail --silent http://127.0.0.1:18765/api/health)"
python3 - "${health}" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
if value.get("version") != "0.14.0":
    raise SystemExit("即时 AI 必须先正式发布到 0.14.0，再激活主人资料库。")
PY

python3 - "${SOURCE_ROOT}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
index = json.loads((root / "public-snapshot.json").read_text(encoding="utf-8"))
counts = index.get("counts") or {}
detail_count = len(list((root / "details").glob("*.json")))
media_files = list((root / "media").rglob("*.mp4"))
if index.get("version") != 2 or counts.get("works") != 398:
    raise SystemExit("主人资料索引版本或作品数量不正确。")
if detail_count != counts.get("works"):
    raise SystemExit("作品详情数量与索引不一致。")
if len(media_files) != 388:
    raise SystemExit("压缩视频数量不是 388。")
if sum(path.stat().st_size for path in media_files) < 1_300_000_000:
    raise SystemExit("压缩视频总量异常偏小。")
PY

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o instantai -g instantai -m 0750 "${TARGET_ROOT}" "${BACKUP_ROOT}/${timestamp}"
if [[ -f "${TARGET_ROOT}/public-snapshot.json" ]]; then
  install -o instantai -g instantai -m 0640 \
    "${TARGET_ROOT}/public-snapshot.json" \
    "${BACKUP_ROOT}/${timestamp}/public-snapshot.json"
fi

mv "${SOURCE_ROOT}/details" "${TARGET_ROOT}/details"
mv "${SOURCE_ROOT}/media" "${TARGET_ROOT}/media"
install -o instantai -g instantai -m 0640 \
  "${SOURCE_ROOT}/public-snapshot.json" \
  "${TARGET_ROOT}/public-snapshot.json"
chown -R instantai:instantai "${TARGET_ROOT}/details" "${TARGET_ROOT}/media"
find "${TARGET_ROOT}/details" -type d -exec chmod 0750 {} +
find "${TARGET_ROOT}/details" -type f -exec chmod 0640 {} +
find "${TARGET_ROOT}/media" -type d -exec chmod 0750 {} +
find "${TARGET_ROOT}/media" -type f -exec chmod 0640 {} +

systemctl restart instant-ai.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:18765/api/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18765/api/health
echo
echo "模型先生主人资料库已激活：398 条作品、388 个压缩视频。"
