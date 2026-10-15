#!/usr/bin/env bash
set -Eeuo pipefail

fail() { printf 'sunmao-community-singapore-verify: ERROR %s\n' "$*" >&2; exit 1; }
json_field() {
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}

for command in curl python3 systemctl; do
  command -v "${command}" >/dev/null 2>&1 || fail "missing command ${command}"
done
systemctl is-active --quiet sunmao-ai-community.service || fail "community service is not active"
systemctl is-active --quiet instant-ai.service || fail "Instant AI is not active"
systemctl is-active --quiet time-compass.service || fail "Time Compass is not active"

local_health="$(curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18808/health/version)"
[[ "$(printf '%s' "${local_health}" | json_field service)" == sunmao-ai-community ]] || fail "loopback service mismatch"
for host in amuyeye.com xn--4pv19va7008a.com; do
  public_health="$(curl --fail --silent --show-error --max-time 10 "https://${host}/health/version")"
  [[ "$(printf '%s' "${public_health}" | json_field service)" == sunmao-ai-community ]] || fail "${host} is not the community"
done

curl --fail --silent --show-error --max-time 10 https://grandpaamu.com/api/health >/dev/null || fail "Instant AI public health failed"
compass_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 https://compass.grandpaamu.com/)"
[[ "${compass_status}" == 200 || "${compass_status}" == 303 ]] || fail "Time Compass public status ${compass_status}"
curl --fail --silent --show-error --max-time 10 https://mcp.amuyeye.com/health >/dev/null || fail "MCP health failed"
mcp_headers="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 10 https://mcp.amuyeye.com/mcp)"
printf '%s' "${mcp_headers}" | grep -Eq '^HTTP/[^ ]+ 401([[:space:]]|$)' || fail "MCP auth status changed"
printf '%s' "${mcp_headers}" | grep -Eiq '^WWW-Authenticate:.*resource_metadata' || fail "MCP OAuth challenge changed"

printf '%s\n' 'sunmao_community_singapore_cutover_verified=true'
