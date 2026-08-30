#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="/var/tmp/instant-ai-model-mr-metadata-upload"
TARGET_ROOT="/var/lib/instant-ai/model-mr"
BACKUP_ROOT="/var/lib/instant-ai/model-mr-backups"

if [[ ${EUID} -ne 0 ]]; then
  echo "必须由 root 更新模型先生主人资料。" >&2
  exit 1
fi
for required in "${SOURCE_ROOT}/public-snapshot.json" "${SOURCE_ROOT}/details" "${TARGET_ROOT}/media"; do
  [[ -e "${required}" ]] || {
    echo "模型先生资料不完整：${required}" >&2
    exit 1
  }
done
if [[ -e "${SOURCE_ROOT}/media" ]]; then
  echo "本次只允许更新索引和详情，暂存目录不得包含视频。" >&2
  exit 1
fi

health="$(curl --fail --silent --show-error http://127.0.0.1:18765/api/health)"
python3 - "${health}" <<'PY'
import json
import sys

value = json.loads(sys.argv[1])
if value.get("version") != "0.15.0":
    raise SystemExit("即时 AI 必须先正式发布到 0.15.0，再更新模型先生资料。")
PY

validation="$(python3 - "${SOURCE_ROOT}" "${TARGET_ROOT}" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
index = json.loads((source / "public-snapshot.json").read_text(encoding="utf-8"))
counts = index.get("counts") or {}
works = index.get("works") or []
details = sorted((source / "details").glob("*.json"))
media = sorted((target / "media").rglob("*.mp4"))
if index.get("version") != 2 or len(works) < 398:
    raise SystemExit("主人资料索引版本或作品数量不正确。")
if len(details) != len(works) or counts.get("works") != len(works):
    raise SystemExit("作品详情数量与索引不一致。")
if len(media) < 388 or sum(path.stat().st_size for path in media) < 1_300_000_000:
    raise SystemExit("现有云端压缩视频异常；已停止，未替换任何资料。")

stock_reports = 0
for path in details:
    text = path.read_text(encoding="utf-8")
    lowered = text.casefold()
    for forbidden in ("raw_json", "source_path", '"cookie"', '"private_path"', "h:\\\\", "/users/"):
        if forbidden in lowered:
            raise SystemExit(f"详情含禁止字段或本机路径：{path.name}")
    value = json.loads(text)
    if value.get("stock_mentions", {}).get("items"):
        stock_reports += 1
if stock_reports < 1:
    raise SystemExit("评股结果为空，拒绝覆盖当前资料。")
print(json.dumps({"works": len(works), "details": len(details), "stock_reports": stock_reports, "media": len(media)}))
PY
)"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="${BACKUP_ROOT}/${timestamp}-metadata"
install -d -o instantai -g instantai -m 0750 "${TARGET_ROOT}" "${backup}"
install -o instantai -g instantai -m 0640 \
  "${TARGET_ROOT}/public-snapshot.json" \
  "${backup}/public-snapshot.json"

rollback_needed=1
rollback() {
  if [[ ${rollback_needed} -eq 1 && -d "${backup}/details" ]]; then
    if [[ -d "${TARGET_ROOT}/details" ]]; then
      mv "${TARGET_ROOT}/details" "${backup}/failed-details"
    fi
    mv "${backup}/details" "${TARGET_ROOT}/details"
    install -o instantai -g instantai -m 0640 \
      "${backup}/public-snapshot.json" \
      "${TARGET_ROOT}/public-snapshot.json"
    systemctl restart instant-ai.service || true
  fi
}
trap rollback EXIT

mv "${TARGET_ROOT}/details" "${backup}/details"
mv "${SOURCE_ROOT}/details" "${TARGET_ROOT}/details"
install -o instantai -g instantai -m 0640 \
  "${SOURCE_ROOT}/public-snapshot.json" \
  "${TARGET_ROOT}/public-snapshot.json"
chown -R instantai:instantai "${TARGET_ROOT}/details"
find "${TARGET_ROOT}/details" -type d -exec chmod 0750 {} +
find "${TARGET_ROOT}/details" -type f -exec chmod 0640 {} +

systemctl restart instant-ai.service
for _ in {1..30}; do
  if curl --fail --silent http://127.0.0.1:18765/api/health >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18765/api/health >/dev/null
rollback_needed=0
trap - EXIT

echo "MODEL_MR_METADATA_UPDATED ${validation}"
echo "backup=${backup}"
echo "media_unchanged=${TARGET_ROOT}/media"
