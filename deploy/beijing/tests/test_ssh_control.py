"""Offline synthetic tests; never connect to a server or run a production publisher."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'deploy/beijing/ssh-control'


def load(name):
    spec = importlib.util.spec_from_file_location('beijing_' + name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load('client')
installer = load('install')
reader = load('status')
A, B, TREE = 'a' * 40, 'b' * 40, 'c' * 40
PUBLIC = 'ssh-ed25519 ' + base64.b64encode(
    struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
).decode()


def receipt(**changes):
    value = dict(schema_version=1, accepted_revision=A, deployed_revision=A,
                 deployed_tree=TREE, failed_revision=None, current_revision=A,
                 live_revision=A, service_state='active', publisher_state='inactive',
                 publisher_result='success', timer_state='inactive', timer_enabled='disabled')
    value.update(changes)
    return value


class SSHControlTests(unittest.TestCase):
    def test_failure_classification_never_echoes_raw_ssh_output(self):
        for error, expected in (
            ('Host key verification failed', 'SSH_HOST_PIN_REJECTED'),
            ('Permission denied (publickey)', 'SSH_AUTHORIZATION_DENIED'),
            ('Connection timed out', 'SSH_NETWORK_UNREACHABLE'),
            ('Connection refused', 'SSH_PORT_REFUSED'),
        ):
            value = client.ssh_failure(error + ' private diagnostic canary')
            self.assertTrue(value.startswith(expected))
            self.assertNotIn('canary', value)

    def test_repo_root_is_exact(self):
        self.assertEqual(client.REPO, ROOT)
        self.assertEqual(installer.REPO, ROOT)

    def test_client_accepts_only_fixed_actions_and_pins_one_host(self):
        with patch.object(client, 'protected_file'):
            args = client.ssh_args('inspect')
            self.assertEqual(args[-2:], ['beijingcodex@47.93.214.76', 'inspect'])
            for option in ('StrictHostKeyChecking=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
                           'ProxyCommand=none', 'ProxyJump=none', 'ControlPath=none',
                           'ClearAllForwardings=yes', 'HostKeyAlgorithms=ssh-ed25519'):
                self.assertIn(option, args)
            self.assertEqual(args[1:3], ['-F', '/dev/null'])
            for action in ('', 'sh', 'scp', 'sftp', 'inspect; whoami', 'publish ' + A):
                with self.subTest(action=action), self.assertRaises(client.ControlError):
                    client.ssh_args(action)

    def test_protected_file_never_opens_private_key(self):
        identity = Mock()
        identity.lstat.return_value = Mock(st_mode=0o100600, st_uid=1000)
        with patch.object(client.os, 'getuid', return_value=1000, create=True):
            client.protected_file(identity)
            identity.open.assert_not_called()
            identity.read_bytes.assert_not_called()
            identity.lstat.return_value.st_mode = 0o100644
            with self.assertRaises(client.ControlError):
                client.protected_file(identity)
            identity.lstat.return_value.st_mode = 0o120600
            with self.assertRaises(client.ControlError):
                client.protected_file(identity)

    def test_dispatch_denies_arbitrary_shell_and_arguments(self):
        bash = Path('D:/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')
        if not bash.exists():
            self.skipTest('No existing Bash runtime')
        for command in ('', 'whoami', 'sh', 'bash -c id', 'sftp', 'scp -t /tmp/x',
                        'inspect;id', 'inspect\nstatus', 'publish extra', '$(id)', 'status '):
            with self.subTest(command=command):
                result = subprocess.run([str(bash), str(SOURCE / 'dispatch.sh')],
                                        env=dict(os.environ, SSH_ORIGINAL_COMMAND=command),
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertEqual(result.stderr.strip(), 'BEIJING_CONTROL_DENIED')
        result = subprocess.run([str(bash), str(SOURCE / 'dispatch.sh'), 'inspect'],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 64)

    def test_status_schema_does_not_accept_extra_content(self):
        for value in ([], {}, dict(receipt(), environment='not-allowed'),
                      receipt(live_revision='not-a-revision'), receipt(service_state='private text')):
            with self.subTest(value=value), patch.object(client, 'remote', return_value=json.dumps(value)):
                with self.assertRaises(client.ControlError):
                    client.status()

    def test_status_schema_accepts_unknown_explicitly(self):
        value = receipt(live_revision=None, publisher_state='unknown')
        with patch.object(client, 'remote', return_value=json.dumps(value)):
            self.assertEqual(client.status(), value)
        self.assertFalse(client.receipt_matches(value, A, TREE))

    def test_receipt_distinguishes_suite_revision_and_unchanged_component(self):
        # Advancing deploy-only files may be accepted without restarting old component A.
        self.assertTrue(client.receipt_matches(receipt(accepted_revision=B), B, TREE))
        for field, bad in [('accepted_revision', B), ('deployed_tree', B), ('current_revision', B),
                           ('live_revision', None), ('service_state', 'failed'),
                           ('publisher_state', 'activating'), ('publisher_result', 'exit-code'),
                           ('timer_state', 'active'), ('timer_enabled', 'enabled')]:
            with self.subTest(field=field):
                self.assertFalse(client.receipt_matches(receipt(**{field: bad}), A, TREE))

    def test_verify_failure_never_retriggers(self):
        with patch.object(client, 'status', return_value=receipt(failed_revision=A)), \
             patch.object(client, 'remote') as remote:
            with self.assertRaises(client.ControlError):
                client.verify(A, TREE, timeout=1)
            remote.assert_not_called()

    def fake_git(self, calls):
        def invoke(*args):
            calls.append(args)
            if args == ('rev-parse', '--show-toplevel'):
                return str(ROOT)
            if args[0] == 'status':
                return ''
            if args[0] == 'remote':
                return 'git@github.com:wangbaocheng123-hash/instant-ai-finance.git'
            if args[0] == 'rev-parse':
                return TREE if ':' in args[1] else (A if 'beijing-production' in args[1] else B)
            if args[0] == 'ls-remote':
                return B + '\trefs/heads/beijing-production'
            return ''
        return invoke

    def test_publish_preflight_then_push_then_one_trigger_then_verified_receipt(self):
        calls = []
        with patch.object(client, 'git', side_effect=self.fake_git(calls)), \
             patch.object(client, 'status', return_value=receipt()), \
             patch.object(client, 'remote', side_effect=[
                 'BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE',
                 'BEIJING_BLOGGER_COLLECTOR_PUBLISH_TRIGGERED']) as remote, \
             patch.object(client, 'verify') as verify:
            client.publish(B)
        self.assertEqual([c.args[0] for c in remote.call_args_list], ['inspect', 'publish'])
        self.assertIn(('push', 'origin', B + ':refs/heads/beijing-production'), calls)
        self.assertFalse(any('--force' in part for call in calls for part in call))
        verify.assert_called_once_with(B, TREE)

    def test_failed_preflight_does_not_advance_production(self):
        for value in (receipt(publisher_state='activating'), receipt(timer_enabled='unknown'),
                      receipt(timer_state='active'), receipt(live_revision=None), receipt(failed_revision=B)):
            calls = []
            with self.subTest(value=value), patch.object(client, 'git', side_effect=self.fake_git(calls)), \
                 patch.object(client, 'status', return_value=value), patch.object(client, 'remote') as remote:
                with self.assertRaises(client.ControlError):
                    client.publish(B)
                self.assertFalse(any(call[0] == 'push' for call in calls))
                remote.assert_not_called()

    def test_uncertain_trigger_does_not_retry_publish(self):
        calls = []
        with patch.object(client, 'git', side_effect=self.fake_git(calls)), \
             patch.object(client, 'status', return_value=receipt()), \
             patch.object(client, 'remote', side_effect=[
                 'BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE', client.ControlError('connection lost')]) as remote:
            with self.assertRaisesRegex(client.ControlError, 'uncertain'):
                client.publish(B)
            self.assertEqual(remote.call_count, 2)

    def test_origin_spoof_is_rejected(self):
        calls = []
        base = self.fake_git(calls)
        with patch.object(client, 'git', side_effect=lambda *args:
                          'https://evil.invalid/wangbaocheng123-hash/instant-ai-finance.git'
                          if args[0] == 'remote' else base(*args)):
            with self.assertRaises(client.ControlError):
                client.publish(B)
        self.assertFalse(any(call[0] == 'push' for call in calls))

    def test_key_options_private_keys_and_wrong_egress_are_refused(self):
        self.assertEqual(installer.public_key(PUBLIC + ' singapore'), PUBLIC)
        for key in ('-----BEGIN OPENSSH PRIVATE KEY-----', 'command="sh" ' + PUBLIC,
                    PUBLIC + '\n' + PUBLIC, 'ssh-ed25519 YQ==', 'ssh-rsa AAAA'):
            with self.subTest(key=key), self.assertRaises(installer.InstallError):
                installer.public_key(key)
        with self.assertRaises(installer.InstallError):
            installer.plans(PUBLIC, '127.0.0.1')

    def test_installer_plan_contains_only_control_files_and_two_sudo_commands(self):
        plan = installer.plans(PUBLIC, installer.SINGAPORE_IP)
        self.assertEqual(len(plan), 7)
        self.assertEqual(set(plan), {installer.DISPATCH, installer.STATUS, installer.KEYS,
                                    installer.DROPIN, installer.SUDOERS, installer.INSPECT, installer.TRIGGER})
        sudoers = plan[installer.SUDOERS][0].decode()
        self.assertNotIn('NOPASSWD: ALL', sudoers)
        self.assertIn('beijing-suite-inspect "",', sudoers)
        self.assertIn('beijing-collector-publish-trigger ""', sudoers)
        self.assertIn('restrict,from="47.236.175.118",command=', plan[installer.KEYS][0].decode())
        self.assertIn('DisableForwarding yes', plan[installer.DROPIN][0].decode())

    def test_effective_sshd_policy_fails_closed(self):
        with self.assertRaises(installer.InstallError):
            installer.validate_effective({'forcecommand': '/bin/sh'})
        with self.assertRaises(installer.InstallError):
            installer.sudo_commands('(ALL : ALL) NOPASSWD: ALL')
        installer.sudo_commands(f'(root) NOPASSWD: {installer.INSPECT} "", {installer.TRIGGER} ""')
        installer.sudo_commands(f'(root) NOPASSWD: {installer.INSPECT} "",\n    {installer.TRIGGER} ""')

    def test_existing_different_control_file_is_not_replaced(self):
        with self.bootstrap_fixture() as state:
            state['target'].write_bytes(b'pre-existing owner configuration')
            with self.assertRaisesRegex(installer.InstallError, 'refused replacement'):
                installer.bootstrap(state['args'])
            self.assertEqual(state['target'].read_bytes(), b'pre-existing owner configuration')
            self.assertFalse(any('useradd' in call[0] for call in state['calls']))

    def test_bootstrap_defaults_to_read_only(self):
        with self.bootstrap_fixture() as state:
            state['args'].apply = False
            installer.bootstrap(state['args'])
            self.assertFalse(state['target'].exists())
            self.assertFalse(any('useradd' in call[0] or 'reload' in call for call in state['calls']))

    def test_policy_failure_rolls_back_new_authorization_without_reload(self):
        with self.bootstrap_fixture() as state, \
             patch.object(installer, 'validate_effective', side_effect=installer.InstallError('policy mismatch')):
            with self.assertRaises(installer.InstallError):
                installer.bootstrap(state['args'])
            self.assertFalse(state['target'].exists())
            self.assertFalse(any('reload' in call for call in state['calls']))

    def test_root_policy_change_rolls_back_before_reload(self):
        with self.bootstrap_fixture() as state, patch.object(installer, 'validate_effective'), \
             patch.object(installer, 'effective', side_effect=[
                 {'usepam': 'yes'}, {'original': 'yes'}, {}, {'unexpected': 'yes'}]):
            with self.assertRaisesRegex(installer.InstallError, 'Global/root'):
                installer.bootstrap(state['args'])
            self.assertFalse(state['target'].exists())
            self.assertFalse(any('reload' in call for call in state['calls']))

    def bootstrap_fixture(self):
        """Simulated root filesystem/commands; never use real useradd, sudo or systemctl."""
        from contextlib import contextmanager

        @contextmanager
        def fixture():
            with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                (root / 'run/lock').mkdir(parents=True)
                key = root / 'dedicated.pub'
                key.write_text(PUBLIC, encoding='ascii')
                target = root / 'control-file'
                args = types.SimpleNamespace(revision=A, public_key_file=key,
                                             source_ip=installer.SINGAPORE_IP, apply=True)
                calls = []
                def fake_run(command, allow_failure=False):
                    calls.append(command)
                    if command[0].endswith('/git') and 'rev-parse' in command:
                        text = A
                    elif '--property=LoadState' in command:
                        text = 'loaded'
                    elif command[0].endswith('/sudo'):
                        text = f'(root) NOPASSWD: {installer.INSPECT} "", {installer.TRIGGER} ""'
                    else:
                        text = ''
                    return Mock(stdout=text, returncode=0)
                stack.enter_context(patch.dict(sys.modules, {
                    'fcntl': types.SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2),
                    'pwd': types.SimpleNamespace(getpwnam=Mock(side_effect=KeyError)),
                }))
                stack.enter_context(patch.object(installer.os, 'geteuid', return_value=0, create=True))
                stack.enter_context(patch.object(installer.os, 'chown', create=True))
                stack.enter_context(patch.object(installer, 'secure_path'))
                stack.enter_context(patch.object(installer, 'effective', return_value={'usepam': 'yes'}))
                stack.enter_context(patch.object(installer, 'run', side_effect=fake_run))
                stack.enter_context(patch.object(installer, 'plans', return_value={target: (b'new policy', 0o644)}))
                stack.enter_context(patch.object(installer, 'HOME_DIR', root / 'control-home'))
                stack.enter_context(patch.object(installer, 'replace', side_effect=lambda p, b, m: p.write_bytes(b)))
                real_open = open
                stack.enter_context(patch.object(installer, 'open', create=True, side_effect=lambda p, *a, **k:
                    real_open(root / 'run/lock/install.lock' if str(p).startswith('/run/lock/') else p, *a, **k)))
                stack.enter_context(redirect_stderr(io.StringIO()))
                stack.enter_context(redirect_stdout(io.StringIO()))
                yield dict(args=args, target=target, calls=calls)
        return fixture()

    def test_reader_filters_service_and_revision_values(self):
        with patch.object(reader.subprocess, 'run', return_value=Mock(returncode=0, stdout='not metadata')):
            self.assertEqual(reader.unit_property('x', 'x', {'active'}), 'unknown')
        self.assertEqual(reader.revision(A), A)
        self.assertIsNone(reader.revision('malformed\n' + A))

    def test_network_reader_uses_loopback_no_redirect_and_no_proxy(self):
        text = (SOURCE / 'status.py').read_text(encoding='utf-8')
        self.assertIn('ProxyHandler({})', text)
        self.assertIn('NoRedirect()', text)
        self.assertIn('http://127.0.0.1:18797/health/version', text)
        self.assertNotIn('/api/collector/status', text)
        self.assertNotIn('.env', text)
        self.assertNotIn('journalctl', text)


if __name__ == '__main__':
    unittest.main()
