#!/usr/bin/env python3
"""Singapore-side operator. No passwords, tokens, eval, shell or arbitrary host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
SUBTREE = 'services/beijing-blogger-collector'
CONFIG = Path.home() / '.config/instant-ai-beijing-control'
ORIGINS = {
    'git@github.com:wangbaocheng123-hash/instant-ai-finance.git',
    'ssh://git@ssh.github.com:443/wangbaocheng123-hash/instant-ai-finance.git',
    'ssh://ssh.github.com:443/wangbaocheng123-hash/instant-ai-finance',
    'https://github.com/wangbaocheng123-hash/instant-ai-finance.git',
}
HEX = re.compile(r'[0-9a-f]{40}\Z')


class ControlError(RuntimeError):
    pass


def validate_revision(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ControlError('A full lowercase 40-character revision is required')
    return value


def protected_file(path):
    # Stat only: the controller never opens the SSH private key.
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise ControlError('SSH_BOOTSTRAP_MISSING: provision the Singapore identity and verified host pin first') from None
    if not stat.S_ISREG(metadata.st_mode):
        raise ControlError('SSH identity/host pin must be a regular file')
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ControlError('SSH identity/host pin requires owner-only permissions')


def ssh_args(action):
    if action not in {'inspect', 'status', 'publish'}:
        raise ControlError('Remote command is not allowlisted')
    identity = CONFIG / 'ssh_identity'
    known_hosts = CONFIG / 'known_hosts'
    protected_file(identity)
    protected_file(known_hosts)
    return [
        '/usr/bin/ssh', '-F', '/dev/null', '-T', '-n', '-p', '22',
        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'HostKeyAlias=beijing-collector-control',
        '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'UpdateHostKeys=no',
        '-o', 'UserKnownHostsFile=' + str(known_hosts),
        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'IdentitiesOnly=yes',
        '-o', 'IdentityAgent=none', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
        '-o', 'ClearAllForwardings=yes', '-o', 'ProxyCommand=none',
        '-o', 'ProxyJump=none', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
        '-i', str(identity), 'beijingcodex@47.93.214.76', action,
    ]


def run(args, timeout=60):
    result = subprocess.run(args, cwd=REPO, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # Git/SSH error bodies may contain local paths or provider details. Don't echo them.
        if Path(args[0]).name == 'ssh':
            raise ControlError(ssh_failure(result.stderr))
        raise ControlError(f'{Path(args[0]).name} failed (exit {result.returncode}); no credentials printed')
    return result.stdout.strip()


def ssh_failure(error):
    lower = error.lower()
    for needles, label in (
        (('host key verification failed', 'remote host identification has changed'), 'SSH_HOST_PIN_REJECTED'),
        (('permission denied',), 'SSH_AUTHORIZATION_DENIED'),
        (('connection timed out', 'no route to host'), 'SSH_NETWORK_UNREACHABLE'),
        (('connection refused',), 'SSH_PORT_REFUSED'),
        (('unprotected private key file', 'bad permissions'), 'SSH_IDENTITY_PERMISSIONS_UNSAFE'),
    ):
        if any(needle in lower for needle in needles):
            return label + ': no fallback, proxy, credential export or automatic retry'
    return 'SSH_FAILED: check the trusted server connection; raw output withheld'


def remote(action):
    return run(ssh_args(action), timeout=60)


def git(*args):
    return run(['/usr/bin/git', *args], timeout=90)


def status():
    value = json.loads(remote('status'))
    fields = {'schema_version', 'accepted_revision', 'deployed_revision', 'deployed_tree',
              'failed_revision', 'current_revision', 'live_revision', 'service_state',
              'publisher_state', 'publisher_result', 'timer_state', 'timer_enabled'}
    if not isinstance(value, dict) or set(value) != fields or value['schema_version'] != 1:
        raise ControlError('Unrecognized Beijing status schema')
    for field in fields - {'schema_version', 'service_state', 'publisher_state',
                            'publisher_result', 'timer_state', 'timer_enabled'}:
        if value[field] is not None:
            validate_revision(value[field])
    states = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'unknown'}
    if any(value[field] not in states for field in ('service_state', 'publisher_state', 'timer_state')):
        raise ControlError('Unrecognized service state')
    if value['timer_enabled'] not in {'disabled', 'masked', 'enabled', 'enabled-runtime', 'static', 'unknown'}:
        raise ControlError('Unrecognized timer state')
    if value['publisher_result'] not in {'success', 'exit-code', 'timeout', 'signal', 'resources', 'unknown'}:
        raise ControlError('Unrecognized publisher result')
    return value


def receipt_matches(value, target, tree):
    deployed = value.get('deployed_revision')
    return (
        value.get('accepted_revision') == target and value.get('deployed_tree') == tree
        and bool(deployed) and value.get('live_revision') == deployed
        and value.get('current_revision') == deployed and value.get('service_state') == 'active'
        and value.get('publisher_state') == 'inactive' and value.get('publisher_result') == 'success'
        and value.get('timer_state') == 'inactive' and value.get('timer_enabled') in {'disabled', 'masked'}
    )


def verify(target, tree, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = status()
        except (ControlError, OSError, ValueError, subprocess.SubprocessError):
            # Only read-only receipt checks retry; never re-trigger on an uncertain response.
            time.sleep(10)
            continue
        if value.get('failed_revision') == target:
            raise ControlError('Target failed; inspect status. No automatic re-trigger or branch rollback')
        if receipt_matches(value, target, tree):
            print('BEIJING_SSH_PUBLISH_VERIFIED')
            print(json.dumps(value, sort_keys=True))
            return
        time.sleep(10)
    raise ControlError('Receipt not confirmed; use verify with the SAME revision. Do not publish again blindly')


def publish(target):
    # Calling this action requires the owner's explicit production release request.
    if git('rev-parse', '--show-toplevel') != str(REPO):
        raise ControlError('Wrong repository root')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise ControlError('Commit or remove unrelated worktree changes first; no release attempted')
    if git('remote', 'get-url', 'origin') not in ORIGINS:
        raise ControlError('Unexpected origin')
    git('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main',
        '+refs/heads/beijing-production:refs/remotes/origin/beijing-production')
    if git('rev-parse', 'HEAD') != target or git('rev-parse', 'refs/remotes/origin/main') != target:
        raise ControlError('Target must exactly match clean HEAD and origin/main')
    previous = validate_revision(git('rev-parse', 'refs/remotes/origin/beijing-production'))
    git('merge-base', '--is-ancestor', previous, target)
    tree = validate_revision(git('rev-parse', target + ':' + SUBTREE))
    initial = status()
    if initial['publisher_state'] not in {'inactive', 'failed'}:
        raise ControlError('Publisher is busy or unknown; production ref was not changed')
    if initial['timer_state'] != 'inactive' or initial['timer_enabled'] not in {'disabled', 'masked'}:
        raise ControlError('Timer must be verified disabled/inactive; do not change it automatically')
    if (initial['service_state'] != 'active' or not initial['live_revision']
            or initial['current_revision'] != initial['live_revision']
            or initial['deployed_revision'] != initial['live_revision']):
        raise ControlError('Current collector health is not confirmed; production ref was not changed')
    if initial['failed_revision'] == target:
        raise ControlError('Known failed target; create a reviewed forward fix instead')
    if receipt_matches(initial, target, tree):
        print('BEIJING_SSH_ALREADY_VERIFIED')
        return
    if 'BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE' not in remote('inspect').splitlines():
        raise ControlError('Read-only wrapper was not verified')
    # Deliberately no --force or force-with-lease. An intervening non-FF update fails.
    git('push', 'origin', target + ':refs/heads/beijing-production')
    if git('ls-remote', '--heads', 'origin', 'beijing-production').split()[0] != target:
        raise ControlError('Production ref changed concurrently; no trigger sent')
    try:
        response = remote('publish')
    except (ControlError, OSError, subprocess.SubprocessError):
        raise ControlError('Trigger response uncertain; production ref was advanced. Use verify, never blind retry') from None
    if response != 'BEIJING_BLOGGER_COLLECTOR_PUBLISH_TRIGGERED':
        raise ControlError('Trigger not acknowledged; production ref was advanced. Use verify')
    verify(target, tree)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'status', 'publish', 'verify'))
    parser.add_argument('revision', nargs='?')
    args = parser.parse_args()
    try:
        if sys.platform != 'linux':
            raise ControlError('Run this client on the Singapore Linux host, not on the Windows/VPN desktop')
        if args.action in {'publish', 'verify'}:
            target = validate_revision(args.revision)
            if args.action == 'publish':
                publish(target)
            else:
                verify(target, validate_revision(git('rev-parse', target + ':' + SUBTREE)))
        elif args.revision is not None:
            raise ControlError('Read-only actions accept no revision/command arguments')
        elif args.action == 'inspect':
            print(remote('inspect'))
        else:
            print(json.dumps(status(), sort_keys=True))
    except (ControlError, OSError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(error, ControlError):
            print('BEIJING_CONTROL_NOT_READY: ' + str(error), file=sys.stderr)
        else:
            print('BEIJING_CONTROL_NOT_READY: ' + type(error).__name__ + '; details withheld', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
