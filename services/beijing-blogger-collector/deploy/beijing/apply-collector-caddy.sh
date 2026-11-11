#!/usr/bin/env bash
# Install the exact Beijing entry after confirming this domain can use Beijing ingress.
set -Eeuo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly TARGET='/etc/caddy/Caddyfile'
readonly SITE='/etc/caddy/collector-grandpaamu.caddy'
readonly IMPORT="import $SITE"
readonly TEMPLATE="$SOURCE_DIR/collector.Caddyfile.example"
readonly BACKUP_DIR='/etc/caddy/backups'

fail() { printf 'COLLECTOR_ENTRY_FAILED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail 'root required'
[[ -f "$TARGET" && ! -L "$TARGET" && -f "$TEMPLATE" && ! -L "$SITE" ]] || fail 'unexpected Caddy layout'
if [[ -e "$SITE" ]]; then
    cmp -s "$TEMPLATE" "$SITE" || fail 'existing collector site differs; review first'
fi
import_count="$(grep -Fxc "$IMPORT" "$TARGET" || true)"
[[ "$import_count" -le 1 ]] || fail 'duplicate collector import'
if [[ "$import_count" == 1 ]]; then
    [[ -f "$SITE" ]] || fail 'import exists without the collector site file'
    caddy validate --adapter caddyfile --config "$TARGET"
    printf 'COLLECTOR_ENTRY_ALREADY_INSTALLED\n'
    exit 0
fi

curl --fail --silent --show-error --max-time 8 http://127.0.0.1:18797/health >/dev/null
[[ -d "$BACKUP_DIR" ]] || install -d -o root -g root -m 0700 "$BACKUP_DIR"
backup="$(mktemp -d "$BACKUP_DIR/collector-entry.XXXXXX")"
cp --preserve=mode,ownership,timestamps -- "$TARGET" "$backup/Caddyfile"
staged="$(mktemp /etc/caddy/.Caddyfile.collector.XXXXXX)"
site_created=false
target_changed=false
restore_previous_config() {
    local result=$?
    trap - ERR
    if [[ "$target_changed" == true ]]; then
        cp --preserve=mode,ownership,timestamps -- "$backup/Caddyfile" "$TARGET" || exit "$result"
        caddy validate --adapter caddyfile --config "$TARGET" && systemctl reload caddy.service || true
    fi
    if [[ "$site_created" == true ]]; then
        mv -- "$SITE" "$backup/failed-collector-grandpaamu.caddy" || true
    fi
    printf 'COLLECTOR_ENTRY_FAILED backup=%s\n' "$backup" >&2
    exit "$result"
}
trap 'rm -f -- "$staged"' EXIT
trap restore_previous_config ERR

if [[ ! -e "$SITE" ]]; then
    site_created=true
    install -o root -g root -m 0644 "$TEMPLATE" "$SITE"
fi
{ cat -- "$backup/Caddyfile"; printf '\n%s\n' "$IMPORT"; } >"$staged"
chown --reference="$TARGET" "$staged"
chmod --reference="$TARGET" "$staged"
caddy validate --adapter caddyfile --config "$staged"
cmp -s "$backup/Caddyfile" "$TARGET"
mv -- "$staged" "$TARGET"
target_changed=true
systemctl reload caddy.service
systemctl is-active --quiet caddy.service
trap - ERR
printf 'COLLECTOR_ENTRY_INSTALLED backup=%s\n' "$backup"
