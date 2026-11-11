#!/usr/bin/env bash
# Install the new English community hostname on the existing Singapore gateway.
# Keep the community import outside the Caddyfile maintained by time-compass.
set -Eeuo pipefail

readonly WRAPPER=/etc/caddy/Caddyfile.instant-ai
readonly SHARED=/etc/caddy/Caddyfile
readonly SITE=/etc/caddy/sunmao-community-grandpaamu.caddy
readonly SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)/sunmao-community-grandpaamu.Caddyfile.example"
readonly IMPORT='import /etc/caddy/sunmao-community-grandpaamu.caddy'
fail() { printf 'SUNMAO_ENTRY_FAILED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail 'root required'
[[ -f "$WRAPPER" && ! -L "$WRAPPER" && -f "$SHARED" ]] || fail 'unexpected Caddy layout'
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || fail 'reviewed site template missing'
[[ ! -L "$SITE" ]] || fail 'community site must not be a symlink'
version="$(/usr/bin/caddy version | cut -d ' ' -f 1 | sed 's/^v//')"
[[ "$(printf '%s\n' 2.11.0 "$version" | sort -V | head -n 1)" == 2.11.0 ]] || fail 'Caddy 2.11+ required for graceful signal reload with admin off'
reload_config() { systemctl kill --kill-whom=main --signal=USR1 caddy.service; }
grep -Fxq 'import /etc/caddy/Caddyfile' "$WRAPPER" || fail 'shared config import missing'
grep -Fxq 'import /etc/caddy/instant-ai.caddy' "$WRAPPER" || fail 'instant-ai import missing'
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:18818/health/version |
    python3 -c 'import json,sys; value=json.load(sys.stdin); assert value["service"]=="sunmao-ai-community" and value["status"]=="ok"'
if [[ -e "$SITE" ]]; then
    [[ ! -L "$SITE" ]] && cmp -s "$SOURCE" "$SITE" || fail 'existing community site differs; review first'
fi
import_count="$(grep -Fxc "$IMPORT" "$WRAPPER" || true)"
[[ "$import_count" -le 1 ]] || fail 'duplicate community import'
if [[ "$import_count" == 1 ]]; then
    [[ -f "$SITE" ]] || fail 'import exists without site file'
    /usr/bin/caddy validate --config "$WRAPPER" --adapter caddyfile
    printf 'SUNMAO_ENTRY_ALREADY_INSTALLED\n'
    exit 0
fi
if grep -Eq '^[[:space:]]*sunmao\.grandpaamu\.com[[:space:]]*\{' "$SHARED"; then
    fail 'new community site already exists in shared file; review first'
fi

readonly SHARED_SHA="$(sha256sum "$SHARED" | cut -d ' ' -f 1)"
[[ -d /etc/caddy/backups ]] || install -d -o root -g root -m 0700 /etc/caddy/backups
readonly BACKUP="$(mktemp -d /etc/caddy/backups/sunmao-entry.XXXXXX)"
cp --preserve=mode,ownership,timestamps -- "$WRAPPER" "$BACKUP/Caddyfile.instant-ai"
readonly STAGED="$(mktemp /etc/caddy/.Caddyfile.sunmao.XXXXXX)"
site_created=false
wrapper_changed=false
rollback() {
    local result=$?
    trap - ERR
    if [[ "$wrapper_changed" == true ]]; then
        cp --preserve=mode,ownership,timestamps -- "$BACKUP/Caddyfile.instant-ai" "$WRAPPER" || exit "$result"
        /usr/bin/caddy validate --config "$WRAPPER" --adapter caddyfile && reload_config || true
    fi
    if [[ "$site_created" == true ]]; then
        mv -- "$SITE" "$BACKUP/failed-sunmao-community-grandpaamu.caddy" || true
    fi
    printf 'SUNMAO_ENTRY_FAILED backup=%s\n' "$BACKUP" >&2
    exit "$result"
}
trap 'rm -f -- "$STAGED"' EXIT
trap rollback ERR
if [[ ! -e "$SITE" ]]; then
    site_created=true
    install -o root -g root -m 0644 "$SOURCE" "$SITE"
fi
{ cat -- "$BACKUP/Caddyfile.instant-ai"; printf '\n%s\n' "$IMPORT"; } >"$STAGED"
chown --reference="$WRAPPER" "$STAGED"
chmod --reference="$WRAPPER" "$STAGED"
/usr/bin/caddy validate --config "$STAGED" --adapter caddyfile
[[ "$(sha256sum "$SHARED" | cut -d ' ' -f 1)" == "$SHARED_SHA" ]]
cmp -s "$BACKUP/Caddyfile.instant-ai" "$WRAPPER"
mv -- "$STAGED" "$WRAPPER"
wrapper_changed=true
reload_config
systemctl is-active --quiet caddy.service
trap - ERR
printf 'SUNMAO_ENTRY_INSTALLED\nbackup=%s\nshared_sha256=%s\n' "$BACKUP" "$SHARED_SHA"
