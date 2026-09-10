#!/bin/sh
# Installed root-owned; never evaluate client-supplied shell text.
set -eu
[ "$#" -eq 0 ] || exit 64
case "${SSH_ORIGINAL_COMMAND-}" in
  inspect)
    exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C \
      HOME=/var/lib/beijing-codex-control \
      /usr/bin/sudo -n -- /usr/local/sbin/beijing-suite-inspect ;;
  status)
    exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C \
      /usr/bin/python3 -I /usr/local/lib/beijing-codex-control/status.py ;;
  publish)
    exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C \
      HOME=/var/lib/beijing-codex-control \
      /usr/bin/sudo -n -- /usr/local/sbin/beijing-collector-publish-trigger ;;
  *) printf 'BEIJING_CONTROL_DENIED\n' >&2; exit 64 ;;
esac
