#!/usr/bin/env bash
set -Eeuo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TARGET='/etc/caddy/Caddyfile'
readonly TEMPLATE="${SOURCE_DIR}/collector.Caddyfile.example"
readonly BACKUP_DIR='/etc/caddy/backups'

[[ "$(id -u)" -eq 0 ]] || {
  printf '%s\n' 'collector_caddy_error=must_run_as_root' >&2
  exit 1
}
[[ -f "${TARGET}" && -f "${TEMPLATE}" ]] || {
  printf '%s\n' 'collector_caddy_error=required_file_missing' >&2
  exit 1
}

install -d -o root -g root -m 0750 "${BACKUP_DIR}"
readonly STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
readonly BACKUP="${BACKUP_DIR}/Caddyfile.${STAMP}"
readonly STAGED="$(mktemp /etc/caddy/.Caddyfile.collector.XXXXXX)"

restore_previous_config() {
  if [[ -f "${BACKUP}" ]]; then
    cp --preserve=mode,ownership,timestamps -- "${BACKUP}" "${TARGET}"
    caddy validate --adapter caddyfile --config "${TARGET}" >/dev/null
    systemctl reload caddy.service
  fi
}

cleanup() {
  rm -f -- "${STAGED}"
}

trap cleanup EXIT
trap 'restore_previous_config' ERR

cp --preserve=mode,ownership,timestamps -- "${TARGET}" "${BACKUP}"

python3 - "${TARGET}" "${TEMPLATE}" "${STAGED}" <<'PY'
from pathlib import Path
import sys

target_path, template_path, staged_path = map(Path, sys.argv[1:])
target = target_path.read_text(encoding="utf-8")
template = template_path.read_text(encoding="utf-8").strip() + "\n"
marker = "collector.amuyeye.com {"

start = target.find(marker)
if start < 0 or target.find(marker, start + len(marker)) >= 0:
    raise SystemExit("collector_caddy_error=site_block_count")

depth = 0
end = None
for index in range(start, len(target)):
    character = target[index]
    if character == "{":
        depth += 1
    elif character == "}":
        depth -= 1
        if depth == 0:
            end = index + 1
            break
if end is None:
    raise SystemExit("collector_caddy_error=unterminated_site_block")

prefix = target[:start].rstrip()
suffix = target[end:].lstrip()
parts = [part for part in (prefix, template.rstrip(), suffix.rstrip()) if part]
Path(staged_path).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
PY

chown --reference="${TARGET}" "${STAGED}"
chmod --reference="${TARGET}" "${STAGED}"
caddy fmt --overwrite "${STAGED}" >/dev/null
caddy validate --adapter caddyfile --config "${STAGED}" >/dev/null
mv -f -- "${STAGED}" "${TARGET}"
systemctl reload caddy.service
[[ "$(systemctl is-active caddy.service)" == 'active' ]]

printf '%s\n' 'collector_caddy_applied=true'
printf 'collector_caddy_backup=%s\n' "${BACKUP}"
