#!/usr/bin/env python3
"""One-time trusted Beijing root bootstrap. Defaults to checking, never to applying."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[2]
USER = 'beijingcodex'
HOME_DIR = Path('/var/lib/beijing-codex-control')
DISPATCH = Path('/usr/local/libexec/beijing-codex-dispatch')
STATUS = Path('/usr/local/lib/beijing-codex-control/status.py')
KEYS = Path('/etc/beijing-codex-control/authorized_keys')
DROPIN = Path('/etc/ssh/sshd_config.d/00-beijing-codex-control.conf')
SUDOERS = Path('/etc/sudoers.d/beijing-codex-control')
INSPECT = Path('/usr/local/sbin/beijing-suite-inspect')
TRIGGER = Path('/usr/local/sbin/beijing-collector-publish-trigger')
SSHD = '/usr/sbin/sshd'
# Confirm this egress address ON Singapore; never copy the Windows/VPN address.
SINGAPORE_IP = '47.236.175.118'


class InstallError(RuntimeError):
    pass


def run(args, allow_failure=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode and not allow_failure:
        raise InstallError(Path(args[0]).name + ' validation failed; output withheld')
    return result


def public_key(text):
    lines = text.strip().splitlines()
    if len(lines) != 1:
        raise InstallError('Exactly one dedicated Ed25519 PUBLIC key is required')
    parts = lines[0].split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise InstallError('Only a bare Ed25519 PUBLIC key is accepted; no options or private key')
    try:
        raw = base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise InstallError('Invalid public key encoding') from None
    expected = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(raw) != 51 or not raw.startswith(expected):
        raise InstallError('Invalid Ed25519 public key structure')
    return 'ssh-ed25519 ' + parts[1]


def ssh_config():
    return f'''# Managed: Beijing restricted maintenance only. Never edit global/root policy.
Match User {USER}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile {KEYS}
    AuthorizedKeysCommand none
    TrustedUserCAKeys none
    ForceCommand {DISPATCH}
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
    MaxSessions 1
Match all
'''.encode()


def plans(key, source_ip):
    if source_ip != SINGAPORE_IP:
        raise InstallError('Singapore egress differs from the approved IP; stop for a scoped review')
    key_line = (f'restrict,from="{source_ip}",command="{DISPATCH}" '
                f'{public_key(key)} beijing-control\n').encode()
    sudoers = (f'Defaults:{USER} !requiretty\n'
               f'{USER} ALL=(root) NOPASSWD: {INSPECT} "", {TRIGGER} ""\n').encode()
    return {
        DISPATCH: ((SOURCE / 'dispatch.sh').read_bytes(), 0o755),
        STATUS: ((SOURCE / 'status.py').read_bytes(), 0o755),
        INSPECT: ((SOURCE.parent / 'inspect-collection-suite.sh').read_bytes(), 0o755),
        TRIGGER: ((SOURCE.parent / 'trigger-blogger-collector-publish.sh').read_bytes(), 0o755),
        KEYS: (key_line, 0o644), DROPIN: (ssh_config(), 0o644), SUDOERS: (sudoers, 0o440),
    }


def secure_path(path):
    """No writable ancestor/symlink may replace a root-owned executable or policy."""
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise InstallError('Unsafe root-controlled path: ' + str(item))


def effective(user):
    value = run([SSHD, '-T', '-C', f'user={user},addr={SINGAPORE_IP},host={SINGAPORE_IP}']).stdout
    return dict(line.split(' ', 1) for line in value.splitlines() if ' ' in line)


def validate_effective(value):
    expected = {
        'authenticationmethods': 'publickey', 'pubkeyauthentication': 'yes',
        'passwordauthentication': 'no', 'kbdinteractiveauthentication': 'no',
        'authorizedkeysfile': str(KEYS), 'authorizedkeyscommand': 'none',
        'trustedusercakeys': 'none', 'forcecommand': str(DISPATCH),
        'disableforwarding': 'yes', 'permittty': 'no', 'permituserrc': 'no',
        'maxsessions': '1', 'permituserenvironment': 'no', 'usepam': 'yes',
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise InstallError('Effective SSH restrictions are incomplete; no login enablement/reload')


def sudo_commands(output):
    entries = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('('):
            entries.append(line)
        elif entries and line:
            entries[-1] += ' ' + line
    expected = {f'(root) NOPASSWD: {INSPECT} "", {TRIGGER} ""'}
    if set(entries) != expected:
        raise InstallError('The control user has unexpected or missing sudo rights')


def replace(path, content, mode):
    fd, temporary = tempfile.mkstemp(prefix='.beijing-control-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
            os.fchown(destination.fileno(), 0, 0)
            os.fchmod(destination.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bootstrap(args):
    import fcntl
    import pwd
    if os.geteuid() != 0:
        raise InstallError('Use the already trusted Beijing root connection for first installation')
    if not re.fullmatch('[0-9a-f]{40}', args.revision):
        raise InstallError('Require a verified full source commit')
    secure_path(SOURCE)
    if run(['/usr/bin/git', '-C', str(REPO), 'rev-parse', 'HEAD']).stdout.strip() != args.revision:
        raise InstallError('Bootstrap source revision mismatch')
    if run(['/usr/bin/git', '-C', str(REPO), 'status', '--porcelain', '--untracked-files=normal']).stdout:
        raise InstallError('Bootstrap checkout must be clean')
    # Refuse a private/large/key-options document before opening any input.
    public_metadata = args.public_key_file.lstat()
    if (args.public_key_file.suffix != '.pub' or not stat.S_ISREG(public_metadata.st_mode)
            or public_metadata.st_size > 1024):
        raise InstallError('Pass only the dedicated .pub file')
    desired = plans(args.public_key_file.read_text(encoding='ascii'), args.source_ip)
    run([SSHD, '-t'])
    baseline_root = effective('root')
    baseline_other = effective('beijing-control-unrelated-user')
    if baseline_root.get('usepam') != 'yes':
        raise InstallError('Locked-key account needs the reviewed PAM setup; do not unlock passwords')
    unit = 'blogger-collector-git-deploy.service'
    if run(['/usr/bin/systemctl', 'show', unit, '--property=LoadState', '--value']).stdout.strip() != 'loaded':
        raise InstallError('The existing fixed publisher is missing; do not create a substitute')
    ssh_units = [u for u in ('ssh.service', 'sshd.service') if run(
        ['/usr/bin/systemctl', 'is-active', '--quiet', u], allow_failure=True).returncode == 0]
    if not ssh_units:
        raise InstallError('No running SSH service; no service will be started or enabled')
    ssh_unit = ssh_units[0]  # Debian often exposes both names as aliases.
    # Fail closed on a host that does not include the standard drop-in directory.
    secure_path(DROPIN.parent)
    created_user = False
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        account = None
    if account:
        if account.pw_dir != str(HOME_DIR) or account.pw_shell != '/bin/sh':
            raise InstallError('Existing beijingcodex differs; review it without overwriting/migrating it')
        if set(os.getgrouplist(USER, account.pw_gid)) != {account.pw_gid}:
            raise InstallError('Existing control user has supplementary groups')
        if run(['/usr/bin/passwd', '-S', USER]).stdout.split()[1] != 'L':
            raise InstallError('Control account must already have a locked password')
        secure_path(HOME_DIR)
    parents = {path.parent for path in desired} | {HOME_DIR}
    for parent in parents:
        secure_path(parent if parent.exists() else parent.parent)
    previous = {}
    for path, (content, mode) in desired.items():
        if path.exists() or path.is_symlink():
            secure_path(path)
            if not path.is_file():
                raise InstallError('Managed target is not a regular file')
            previous[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            if previous[path] != (content, mode):
                raise InstallError('Existing control file differs; refused replacement: ' + str(path))
        else:
            previous[path] = None
    if account:
        rights = run(['/usr/bin/sudo', '-n', '-l', '-U', USER], allow_failure=True)
        no_rights = (rights.returncode == 1 and previous[SUDOERS] is None
                     and f'User {USER} is not allowed to run sudo' in (rights.stdout + rights.stderr))
        if not no_rights:
            sudo_commands(rights.stdout)
    if not args.apply:
        print('BEIJING_SSH_BOOTSTRAP_PREFLIGHT_OK apply=false (effective policy still requires installation verification)')
        return
    # Never run concurrently with another installer.
    with open('/run/lock/beijing-codex-control-install.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path, old in previous.items():
            if old is None and (path.exists() or path.is_symlink()):
                raise InstallError('A target appeared during preflight; rerun read-only preflight')
        if account is None:
            try:
                pwd.getpwnam(USER)
            except KeyError:
                pass
            else:
                raise InstallError('Control user appeared concurrently; rerun preflight')
        changed = []
        reloaded = False
        try:
            for parent in sorted(parents, key=lambda p: len(p.parts)):
                if not parent.exists():
                    parent.mkdir(mode=0o755)
                    os.chown(parent, 0, 0)
                    os.chmod(parent, 0o755)
            if account is None:
                run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                     '--home-dir', str(HOME_DIR), '--shell', '/bin/sh', USER])
                created_user = True
            for path, (content, mode) in desired.items():
                if previous[path] is None:
                    replace(path, content, mode)
                    changed.append(path)
            run(['/usr/sbin/visudo', '-cf', str(SUDOERS)])
            run(['/usr/sbin/visudo', '-c'])
            sudo_commands(run(['/usr/bin/sudo', '-n', '-l', '-U', USER]).stdout)
            run([SSHD, '-t'])
            validate_effective(effective(USER))
            if effective('root') != baseline_root or effective('beijing-control-unrelated-user') != baseline_other:
                raise InstallError('Global/root SSH behavior changed; reverting before enablement')
            # Exercise the dispatcher without a key or any production action.
            denied = run(['/usr/sbin/runuser', '-u', USER, '--', '/usr/bin/env',
                          'SSH_ORIGINAL_COMMAND=whoami', str(DISPATCH)], allow_failure=True)
            if denied.returncode != 64:
                raise InstallError('Negative command allowlist check failed')
            for action in ('inspect', 'status'):
                result = run(['/usr/sbin/runuser', '-u', USER, '--', '/usr/bin/env',
                              'SSH_ORIGINAL_COMMAND=' + action, str(DISPATCH)])
                if action == 'inspect' and 'BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE' not in result.stdout:
                    raise InstallError('Read-only wrapper did not complete')
                if action == 'status' and json.loads(result.stdout).get('schema_version') != 1:
                    raise InstallError('Status wrapper did not complete')
            if changed:
                reloaded = True
                run(['/usr/bin/systemctl', 'reload', ssh_unit])
            print('BEIJING_READONLY_CLOUD_CONTROL_READY user=beijingcodex')
            print('BEIJING_RESTRICTED_SSH_INSTALLED (Singapore SSH login NOT YET VERIFIED)')
        except Exception:
            for path in reversed(changed):
                # Only files created by this transaction; never delete user files/directories.
                path.unlink()
            if reloaded:
                run(['/usr/bin/systemctl', 'reload', ssh_unit], allow_failure=True)
            if created_user:
                print('BOOTSTRAP_PARTIAL: locked account retained; new authorization files removed', file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--public-key-file', required=True, type=Path)
    parser.add_argument('--source-ip', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if sys.platform != 'linux':
            raise InstallError('Bootstrap runs only on the verified Beijing Linux server')
        bootstrap(args)
    except Exception as error:
        if isinstance(error, InstallError):
            print('BEIJING_SSH_BOOTSTRAP_STOPPED: ' + str(error), file=sys.stderr)
        else:
            print('BEIJING_SSH_BOOTSTRAP_STOPPED: ' + type(error).__name__ + '; details withheld', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
