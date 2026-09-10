#!/usr/bin/env bash
set -Eeuo pipefail

[[ "$#" -eq 0 ]] || {
  printf 'trigger-blogger-collector-publish: arguments are not accepted\n' >&2
  exit 2
}
[[ "$(id -u)" -eq 0 ]] || {
  printf 'trigger-blogger-collector-publish: root execution required\n' >&2
  exit 1
}

systemctl start --no-block blogger-collector-git-deploy.service
printf 'BEIJING_BLOGGER_COLLECTOR_PUBLISH_TRIGGERED\n'
