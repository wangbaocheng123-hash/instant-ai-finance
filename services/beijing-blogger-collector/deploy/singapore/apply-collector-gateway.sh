#!/usr/bin/env bash
# One-time ingress installation. Business applications use their fixed publishers.
set -Eeuo pipefail

readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY="$(cd -- "$SOURCE_DIR/../../../.." && pwd)"
readonly WRAPPER='/etc/caddy/Caddyfile.instant-ai'
readonly SITE='/etc/caddy/collector-grandpaamu.caddy'
readonly UNIT='/etc/systemd/system/collector-beijing-gateway.service'
readonly SERVICE='collector-beijing-gateway.service'
readonly IMPORT="import $SITE"
readonly CONTROL='/home/compassdev/.config/instant-ai-beijing-control'

fail() { printf 'COLLECTOR_GATEWAY_FAILED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail 'root required'
[[ "${1:-}" == '' || "${1:-}" == '--check' ]] || fail 'usage: apply-collector-gateway.sh [--check]'
[[ -f "$WRAPPER" && ! -L "$WRAPPER" ]] || fail 'unexpected shared wrapper'
[[ ! -e "$SITE" && ! -L "$SITE" && ! -e "$UNIT" && ! -L "$UNIT" ]] || fail 'existing gateway files require review'
! grep -Fq "$SITE" "$WRAPPER" || fail 'collector import already present'
for required in /etc/caddy/Caddyfile /etc/caddy/instant-ai.caddy /etc/caddy/sunmao-community.caddy; do
    grep -Fxq "import $required" "$WRAPPER" || fail "missing existing import: $required"
done
[[ -f "$CONTROL/ssh_identity" && -f "$CONTROL/known_hosts" ]] || fail 'existing Beijing control identity is unavailable'
[[ -z "$(ss -H -ltn '( sport = :18817 or sport = :18819 )')" ]] || fail 'gateway ports already occupied'
git_read() { git -c safe.directory="$REPOSITORY" -C "$REPOSITORY" "$@"; }
case "$(git_read remote get-url origin)" in
    https://github.com/wangbaocheng123-hash/instant-ai-finance.git|git@github.com:wangbaocheng123-hash/instant-ai-finance.git) ;;
    *) fail 'unexpected source origin' ;;
esac
[[ "$(git_read branch --show-current)" == main ]] || fail 'source branch must be main'
[[ -z "$(git_read status --porcelain)" ]] || fail 'source checkout is dirty'
[[ "$(git_read rev-parse HEAD)" == "$(git_read rev-parse origin/main)" ]] || fail 'source is not synchronized with origin/main'
readonly REVISION="$(git_read rev-parse HEAD)"

staged="$(mktemp -d /run/collector-gateway.XXXXXX)"
cleanup() { rm -f -- "$staged/collector-beijing-gateway.service" "$staged/collector-grandpaamu.caddy" "$staged/Caddyfile.check" "$staged/Caddyfile.install"; rmdir -- "$staged"; }
trap cleanup EXIT
cp -- "$SOURCE_DIR/collector-beijing-gateway.service" "$staged/collector-beijing-gateway.service"
python3 "$SOURCE_DIR/render-collector-gateway.py" >"$staged/collector-grandpaamu.caddy"
systemd-analyze verify "$staged/collector-beijing-gateway.service"
{ cat -- "$WRAPPER"; printf '\nimport %s\n' "$staged/collector-grandpaamu.caddy"; } >"$staged/Caddyfile.check"
{ cat -- "$WRAPPER"; printf '\n%s\n' "$IMPORT"; } >"$staged/Caddyfile.install"
caddy validate --adapter caddyfile --config "$staged/Caddyfile.check"
if [[ "${1:-}" == '--check' ]]; then
    printf 'COLLECTOR_GATEWAY_CHECK_OK revision=%s\n' "$REVISION"
    exit 0
fi

install -d -o root -g root -m 0700 /etc/caddy/backups
backup="$(mktemp -d /etc/caddy/backups/collector-gateway.XXXXXX)"
cp --preserve=mode,ownership,timestamps -- "$WRAPPER" "$backup/Caddyfile.instant-ai"
printf '%s\n' "$REVISION" >"$backup/source-revision"
wrapper_changed=false
unit_created=false
site_created=false
rollback() {
    local result=$?
    trap - ERR
    if [[ "$wrapper_changed" == true ]]; then
        cp --preserve=mode,ownership,timestamps -- "$backup/Caddyfile.instant-ai" "$WRAPPER"
        caddy validate --adapter caddyfile --config "$WRAPPER" && systemctl restart caddy.service || true
    fi
    if [[ "$unit_created" == true ]]; then
        systemctl disable --now "$SERVICE" || true
        mv -- "$UNIT" "$backup/failed-collector-beijing-gateway.service" || true
        systemctl daemon-reload || true
    fi
    if [[ "$site_created" == true ]]; then
        mv -- "$SITE" "$backup/failed-collector-grandpaamu.caddy" || true
    fi
    printf 'COLLECTOR_GATEWAY_ROLLED_BACK backup=%s\n' "$backup" >&2
    exit "$result"
}
trap rollback ERR

cmp -s "$backup/Caddyfile.instant-ai" "$WRAPPER"
unit_created=true
install -o root -g root -m 0644 "$staged/collector-beijing-gateway.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
for attempt in {1..12}; do
    if curl --fail --silent --max-time 3 http://127.0.0.1:18817/health/version >/dev/null; then break; fi
    sleep 1
done
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:18817/health/version
model_status="$(curl --silent --show-error --max-time 8 --output /dev/null --write-out '%{http_code}' http://127.0.0.1:18819/)"
[[ "$model_status" == 302 ]] || { printf 'Unexpected model login HTTP %s\n' "$model_status" >&2; false; }
site_created=true
install -o root -g root -m 0644 "$staged/collector-grandpaamu.caddy" "$SITE"
cmp -s "$backup/Caddyfile.instant-ai" "$WRAPPER"
chown --reference="$WRAPPER" "$staged/Caddyfile.install"
chmod --reference="$WRAPPER" "$staged/Caddyfile.install"
wrapper_changed=true
cp --preserve=mode,ownership -- "$staged/Caddyfile.install" "$WRAPPER"
caddy validate --adapter caddyfile --config "$WRAPPER"
# The shared Compass configuration explicitly has `admin off`.
systemctl restart caddy.service
systemctl is-active --quiet caddy.service
systemctl is-active --quiet "$SERVICE"
curl --fail --silent --show-error --max-time 8 http://127.0.0.1:18817/health/version >/dev/null
trap - ERR
printf '\nCOLLECTOR_GATEWAY_INSTALLED revision=%s backup=%s\n' "$REVISION" "$backup"
