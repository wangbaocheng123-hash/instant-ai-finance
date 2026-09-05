#!/usr/bin/env bash
set -euo pipefail

bundle_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
static_dir="${bundle_dir}/static"

check_png() {
  local file_name="$1"
  local expected_size="$2"
  local actual

  actual="$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,pix_fmt \
    -of csv=p=0 "${static_dir}/${file_name}")"
  if [[ "${actual}" != "${expected_size},${expected_size},rgb24" ]]; then
    echo "图标格式错误：${file_name} -> ${actual}" >&2
    exit 1
  fi
}

check_png "north-pole-collector-icon-1024.png" 1024
check_png "north-pole-collector-icon-512.png" 512
check_png "north-pole-collector-icon-192.png" 192
check_png "apple-touch-icon.png" 180
check_png "favicon-32.png" 32

jq -e '
  .id == "/" and
  .name == "北极采集器" and
  .short_name == "北极采集" and
  .display == "standalone" and
  ([.icons[].sizes] | sort) == ["192x192", "512x512"] and
  all(.icons[]; .type == "image/png" and .purpose == "any")
' "${static_dir}/manifest.webmanifest" >/dev/null

rg -q 'rel="apple-touch-icon"[^>]+apple-touch-icon\.png' "${bundle_dir}/head-snippet.html"
rg -q 'rel="manifest"[^>]+manifest\.webmanifest' "${bundle_dir}/head-snippet.html"
rg -q 'apple-mobile-web-app-title" content="北极采集器"' "${bundle_dir}/head-snippet.html"

echo "BEIJING_NORTH_POLE_COLLECTOR_ICON_ASSETS_OK"
