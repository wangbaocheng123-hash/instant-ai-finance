#!/usr/bin/env python3
"""Read only fixed deploy metadata; no logs, environment or business files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import urllib.request

STATE = Path('/var/lib/blogger-agent/git-deploy')
CURRENT = Path('/opt/blogger-agent/current')
HEX = re.compile(r'[0-9a-f]{40}\Z')


def revision(value):
    return value if isinstance(value, str) and HEX.fullmatch(value) else None


def read_revision(name):
    # Only callers below choose names; never an argument received over SSH.
    try:
        fd = os.open(STATE / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return None
            return revision(source.read(128).decode('ascii').strip())
    except (OSError, UnicodeError):
        return None


def unit_property(unit, prop, allowed):
    try:
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', unit, '--property=' + prop, '--value'],
            capture_output=True, text=True, timeout=5, check=False,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and value in allowed else 'unknown'
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_version():
    # Ignore inherited HTTP proxy variables. Never follow a redirect off loopback.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open('http://127.0.0.1:18797/health/version', timeout=5) as response:
            data = response.read(8193)
        if len(data) > 8192:
            return None
        value = json.loads(data)
        if value.get('service') == 'blogger-collector' and value.get('status') == 'ok':
            return revision(value.get('repository_revision'))
    except (OSError, ValueError, AttributeError):
        pass
    return None


def snapshot():
    try:
        target = Path(os.readlink(CURRENT))
        current = revision(target.name) if target.parent in (
            Path('/opt/blogger-agent/git-releases'), Path('/opt/blogger-agent/releases')
        ) else None
    except OSError:
        current = None
    active = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'}
    return {
        'schema_version': 1,
        'accepted_revision': read_revision('accepted-revision'),
        'deployed_revision': read_revision('deployed-revision'),
        'deployed_tree': read_revision('deployed-tree'),
        'failed_revision': read_revision('failed-revision'),
        'current_revision': current,
        'live_revision': live_version(),
        'service_state': unit_property('blogger-collector.service', 'ActiveState', active),
        'publisher_state': unit_property('blogger-collector-git-deploy.service', 'ActiveState', active),
        'publisher_result': unit_property('blogger-collector-git-deploy.service', 'Result',
                                          {'success', 'exit-code', 'timeout', 'signal', 'resources'}),
        'timer_state': unit_property('blogger-collector-git-deploy.timer', 'ActiveState', active),
        'timer_enabled': unit_property('blogger-collector-git-deploy.timer', 'UnitFileState',
                                       {'disabled', 'masked', 'enabled', 'enabled-runtime', 'static'}),
    }


if __name__ == '__main__':
    if len(sys.argv) != 1:
        sys.exit(64)
    print(json.dumps(snapshot(), sort_keys=True))
