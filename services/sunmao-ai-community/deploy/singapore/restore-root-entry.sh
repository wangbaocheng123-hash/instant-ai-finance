#!/usr/bin/env bash
# Run as root on the existing Singapore gateway, beside the reviewed site template.
# Keep the community import outside the Caddyfile maintained by time-compass.
set -Eeuo pipefail

readonly WRAPPER=/etc/caddy/Caddyfile.instant-ai
readonly SHARED=/etc/caddy/Caddyfile
readonly SITE=/etc/caddy/sunmao-community.caddy
readonly SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)/sunmao-community-root.Caddyfile.example"
readonly IMPORT='import /etc/caddy/sunmao-community.caddy'
fail() { printf 'SUNMAO_ENTRY_FAILED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail 'root required'
[[ -f "$WRAPPER" && ! -L "$WRAPPER" && -f "$SHARED" ]] || fail 'unexpected Caddy layout'
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || fail 'reviewed site template missing'
version="$(/usr/bin/caddy version | cut -d ' ' -f 1 | sed 's/^v//')"
[[ "$(printf '%s\n' 2.11.0 "$version" | sort -V | head -n 1)" == 2.11.0 ]] || fail 'Caddy 2.11+ required for graceful signal reload with admin off'
reload_config() { systemctl kill --kill-whom=main --signal=USR1 caddy.service; }
grep -Fxq 'import /etc/caddy/Caddyfile' "$WRAPPER" || fail 'shared config import missing'
grep -Fxq 'import /etc/caddy/instant-ai.caddy' "$WRAPPER" || fail 'instant-ai import missing'
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:18818/health/version >/dev/null
if [[ -e "$SITE" ]]; then
    [[ ! -L "$SITE" ]] && cmp -s "$SOURCE" "$SITE" || fail 'existing community site differs; review first'
fi
if grep -Fxq "$IMPORT" "$WRAPPER"; then
    [[ -f "$SITE" ]] || fail 'import exists without site file'
    printf 'SUNMAO_ENTRY_ALREADY_INSTALLED\n'
    exit 0
fi
if grep -Eq '^[[:space:]]*amuyeye\.com[[:space:]]*\{' "$SHARED"; then
    fail 'root site already exists in shared file; migrate explicitly'
fi

readonly SHARED_SHA="$(sha256sum "$SHARED" | cut -d ' ' -f 1)"
readonly BACKUP="/etc/caddy/backups/sunmao-entry-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o root -g root -m 0700 "$BACKUP"
install -o root -g root -m 0600 "$WRAPPER" "$BACKUP/Caddyfile.instant-ai"
site_created=false
if [[ ! -e "$SITE" ]]; then
    install -o root -g root -m 0644 "$SOURCE" "$SITE"
    site_created=true
fi
rollback() {
    local result=$?
    trap - ERR
    install -o root -g root -m 0644 "$BACKUP/Caddyfile.instant-ai" "$WRAPPER"
    reload_config || true
    if [[ "$site_created" == true ]]; then
        mv -- "$SITE" "$BACKUP/failed-sunmao-community.caddy"
    fi
    printf 'SUNMAO_ENTRY_ROLLED_BACK backup=%s\n' "$BACKUP" >&2
    exit "$result"
}
trap rollback ERR
printf '\n%s\n' "$IMPORT" >>"$WRAPPER"
/usr/bin/caddy validate --config "$WRAPPER" --adapter caddyfile
[[ "$(sha256sum "$SHARED" | cut -d ' ' -f 1)" == "$SHARED_SHA" ]]
reload_config
systemctl is-active --quiet caddy.service
healthy=false
for attempt in {1..12}; do
    if curl --fail --silent --show-error --resolve amuyeye.com:443:127.0.0.1 --connect-timeout 2 --max-time 4 https://amuyeye.com/health/version >"$BACKUP/version.json" 2>/dev/null; then
        healthy=true
        break
    fi
    sleep 1
done
[[ "$healthy" == true ]]
trap - ERR
printf 'SUNMAO_ENTRY_INSTALLED\nbackup=%s\nshared_sha256=%s\n' "$BACKUP" "$SHARED_SHA"
