from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'deploy/aliyun/configure-model-mr-text.py'
SPEC = importlib.util.spec_from_file_location('text_setup', SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class TextSetupTests(unittest.TestCase):
    def test_payload_only_contains_text_fields(self):
        fake = 'SYNTHETIC_TEST_VALUE_NOT_A_KEY'
        value = setup.credential_payload(fake, fake).decode()
        self.assertEqual(len(value.splitlines()), 2)
        self.assertIn('INSTANT_AI_DOUBAO_ARK_API_KEY=', value)
        self.assertNotIn('ASR', value)

    def test_rejects_injection_and_bad_input_without_echo(self):
        for fake in ['short', 'SECRET\nOTHER=anything', 'x' * 513, ' ' * 32, '密钥' * 20]:
            with self.subTest(fake_length=len(fake)), self.assertRaises(setup.SetupError) as result:
                setup.credential_payload(fake, fake)
            self.assertNotIn(fake, str(result.exception))

    def test_mismatched_confirmation(self):
        with self.assertRaises(setup.SetupError):
            setup.credential_payload('SYNTHETIC_TEST_VALUE_ONE', 'SYNTHETIC_TEST_VALUE_TWO')

    def test_existing_configuration_stops_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'synthetic-config.txt'
            existing.write_text('unrelated synthetic configuration', encoding='utf-8')
            with patch.object(setup, 'CONFIG_FILE', existing), patch.object(setup, 'write_new') as writer:
                with self.assertRaises(setup.SetupError):
                    setup.install_pair(b'synthetic')
                writer.assert_not_called()

    def test_partial_install_rolls_back_only_own_new_file(self):
        identity = (123, 456)
        with patch.object(setup, 'require_absent'), \
                patch.object(setup, 'write_new', side_effect=[identity, OSError('simulated')]), \
                patch.object(setup, 'remove_owned_new') as remove:
            with self.assertRaises(OSError):
                setup.install_pair(b'synthetic')
            remove.assert_called_once_with(setup.CONFIG_FILE, identity)

    @unittest.skipUnless(os.name == 'posix', 'Linux-only file mode protection')
    def test_exclusive_private_write_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.os, 'fchown'):
            target = Path(directory) / 'synthetic-config.txt'
            identity = setup.write_new(target, b'SYNTHETIC', 0o600)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                setup.write_new(target, b'other', 0o600)
            setup.remove_owned_new(target, (0, 0))
            self.assertTrue(target.exists())
            setup.remove_owned_new(target, identity)
            self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == 'posix', 'Linux symlink protection')
    def test_symlink_conflict_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'synthetic-link'
            link.symlink_to(Path(directory) / 'does-not-exist')
            with self.assertRaises(setup.SetupError):
                setup.require_absent(link)

    def test_noninteractive_refuses_before_prompt_or_write(self):
        with patch.object(setup.sys, 'argv', ['setup']), \
                patch.object(setup.os, 'geteuid', return_value=0, create=True), \
                patch.object(setup.sys.stdin, 'isatty', return_value=False), \
                patch.object(setup.getpass, 'getpass') as prompt, \
                patch.object(setup, 'install_pair') as install, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(), 2)
            prompt.assert_not_called()
            install.assert_not_called()

    def test_no_secret_arguments(self):
        with patch.object(setup.sys, 'argv', ['setup', 'SYNTHETIC_SECRET_ARGUMENT']), \
                contextlib.redirect_stderr(io.StringIO()) as captured:
            self.assertEqual(setup.main(), 2)
            self.assertNotIn('SYNTHETIC_SECRET_ARGUMENT', captured.getvalue())

    def test_terminal_cancellation_does_not_prompt_for_key(self):
        with patch.object(setup.sys, 'argv', ['setup']), \
                patch.object(setup.os, 'geteuid', return_value=0, create=True), \
                patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                patch.object(setup.sys.stderr, 'isatty', return_value=True), \
                patch.object(setup, 'check_directory'), patch.object(setup, 'require_absent'), \
                patch('builtins.input', return_value='CANCEL'), \
                patch.object(setup.getpass, 'getpass') as prompt, \
                patch.object(setup, 'install_pair') as install, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.main(), 0)
            prompt.assert_not_called()
            install.assert_not_called()

    def test_unsafe_directory_rejected(self):
        from types import SimpleNamespace
        import stat
        bad = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)
        with patch.object(Path, 'lstat', return_value=bad), self.assertRaises(setup.SetupError):
            setup.check_directory(Path('/synthetic'))

    def test_success_only_reloads_definitions_never_echoes_key(self):
        from types import SimpleNamespace
        fake = 'SYNTHETIC_TEST_VALUE_NOT_A_KEY'
        with patch.object(setup.sys, 'argv', ['setup']), \
                patch.object(setup.os, 'geteuid', return_value=0, create=True), \
                patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                patch.object(setup.sys.stderr, 'isatty', return_value=True), \
                patch.object(setup, 'check_directory'), patch.object(setup, 'require_absent'), \
                patch.object(Path, 'mkdir'), patch('builtins.input', return_value='SETUP'), \
                patch.object(setup.getpass, 'getpass', side_effect=[fake, fake]) as prompt, \
                patch.object(setup, 'install_pair') as install, \
                patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(setup.main(), 0)
            self.assertEqual(prompt.call_count, 2)
            install.assert_called_once()
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'daemon-reload'])
            self.assertNotIn(fake, captured.getvalue())
            self.assertIn('TEXT_CONFIG_SAVED', captured.getvalue())

    def test_fixed_separate_environment_file_and_no_restart(self):
        self.assertNotEqual(setup.CONFIG_FILE.name, 'model-mr-secrets.env')
        self.assertEqual(setup.DROPIN, '[Service]\nEnvironmentFile=/etc/instant-ai/model-mr-text-secrets.env\n')
        source = SCRIPT.read_text(encoding='utf-8')
        self.assertNotIn('systemctl\', \'restart', source)
        self.assertNotIn('read_text(', source)
        self.assertNotIn('read_bytes(', source)
        self.assertNotIn('urllib', source)


if __name__ == '__main__':
    unittest.main()
