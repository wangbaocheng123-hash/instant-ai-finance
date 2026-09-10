"""Repository-level contracts, run before release; not part of the collector archive."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ROOT_DEPLOY = REPOSITORY_ROOT / "deploy" / "beijing"


class CloudControlContractTests(unittest.TestCase):
    def test_operator_scripts_fetch_named_remote_tracking_refs(self) -> None:
        expected_main = (
            "+refs/heads/main:refs/remotes/origin/main"
        )
        expected_production = (
            "+refs/heads/beijing-production:"
            "refs/remotes/origin/beijing-production"
        )
        check_script = (ROOT_DEPLOY / "check-publish-channel.sh").read_text(
            encoding="utf-8"
        )
        publish_script = (ROOT_DEPLOY / "publish-via-git.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(expected_main, check_script)
        self.assertIn(expected_production, check_script)
        self.assertIn(expected_main, publish_script)
        self.assertIn(expected_production, publish_script)
        self.assertNotIn(
            "git fetch --no-tags origin main beijing-production",
            check_script,
        )
        self.assertNotIn(
            "git fetch --no-tags origin beijing-production",
            publish_script,
        )

    def test_collection_suite_manifest_is_unified_and_manual(self) -> None:
        manifest = json.loads(
            (ROOT_DEPLOY / "collection-suite.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["production_branch"], "beijing-production")
        self.assertEqual(manifest["deployment"]["mode"], "unified_manual")
        self.assertFalse(manifest["deployment"]["timer_enabled"])
        self.assertEqual(
            manifest["deployment"]["publisher"],
            "/usr/local/sbin/beijing-suite-publish",
        )
        components = {
            item["component_id"]: item for item in manifest["components"]
        }
        self.assertEqual(
            components["blogger-collector"]["source_state"], "managed"
        )
        self.assertEqual(
            components["model-downloader"]["source_state"], "managed"
        )
        self.assertFalse(manifest["integration"]["shared_process"])
        self.assertFalse(manifest["integration"]["shared_database"])

    def test_full_admin_and_suite_publishers_are_explicit(self) -> None:
        installer = (ROOT_DEPLOY / "install-full-admin-control.sh").read_text(
            encoding="utf-8"
        )
        suite = (ROOT_DEPLOY / "beijing-suite-publish").read_text(
            encoding="utf-8"
        )
        model = (ROOT_DEPLOY / "model-downloader-git-deploy").read_text(
            encoding="utf-8"
        )
        self.assertIn("singaporecodex", installer)
        self.assertIn("NOPASSWD: ALL", installer)
        self.assertIn("blogger-collector-git-deploy.service", suite)
        self.assertIn("model-downloader-git-deploy", suite)
        self.assertIn("services/beijing-model-downloader", model)
        self.assertIn("model-downloader-web.service", model)
        self.assertIn("fresh_video_scan_succeeded", model)
        self.assertIn("wait_for_release_health", model)
        self.assertIn("id > ? AND success = 1", model)
        self.assertNotIn(".env", json.dumps({"suite": suite}))

    def test_model_services_cannot_inherit_temporary_proxy_state(self) -> None:
        component = REPOSITORY_ROOT / "services/beijing-model-downloader"
        required = (
            "UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY "
            "http_proxy https_proxy all_proxy"
        )
        for filename in (
            "model-downloader.service",
            "model-downloader-web.service",
        ):
            unit = (
                component / "deploy/beijing" / filename
            ).read_text(encoding="utf-8")
            self.assertIn(required, unit)

    def test_cloud_assistant_policy_is_instance_and_username_scoped(self) -> None:
        policy = json.loads(
            (ROOT_DEPLOY / "ram-cloud-assistant-policy.example.json").read_text(
                encoding="utf-8"
            )
        )
        statements = policy["Statement"]
        run_statement = next(
            item for item in statements if "ecs:RunCommand" in item["Action"]
        )
        self.assertEqual(
            run_statement["Resource"],
            [
                "acs:ecs:cn-beijing:<ACCOUNT_ID>:instance/"
                "<BEIJING_INSTANCE_ID>"
            ],
        )
        self.assertEqual(
            run_statement["Condition"]["StringEquals"]["ecs:CommandRunAs"],
            ["beijingcodex"],
        )
        serialized = json.dumps(policy, sort_keys=True)
        self.assertNotIn('"ecs:*"', serialized)
        self.assertNotIn("AccessKey", serialized)
        self.assertNotIn("AttachInstanceRamRole", serialized)
        self.assertNotIn("DeleteInstance", serialized)

    def test_cloud_inventory_does_not_dump_runtime_or_business_content(self) -> None:
        inventory = (ROOT_DEPLOY / "inspect-collection-suite.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("BEIJING_COLLECTION_SUITE_INVENTORY_COMPLETE", inventory)
        self.assertIn("--untracked-files=no", inventory)
        self.assertIn("tracked_change_count", inventory)
        self.assertNotIn("systemctl cat", inventory)
        self.assertNotIn("/proc/${main_pid}/cmdline", inventory)
        self.assertNotIn("show-environment", inventory)
        self.assertNotIn("EnvironmentFile", inventory)
        self.assertNotIn("sqlite3", inventory)
        self.assertNotIn("comments", inventory)

    def test_cloud_control_bootstrap_only_installs_fixed_wrappers(self) -> None:
        installer = (ROOT_DEPLOY / "install-readonly-cloud-control.sh").read_text(
            encoding="utf-8"
        )
        trigger = (
            ROOT_DEPLOY / "trigger-blogger-collector-publish.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('CONTROL_USER="beijingcodex"', installer)
        self.assertIn("visudo -cf", installer)
        self.assertIn("beijing-suite-inspect", installer)
        self.assertIn("beijing-collector-publish-trigger", installer)
        self.assertNotIn("NOPASSWD: ALL", installer)
        self.assertNotIn("model-downloader.service", trigger)
        self.assertEqual(
            trigger.count(
                "systemctl start --no-block "
                "blogger-collector-git-deploy.service"
            ),
            1,
        )

    def test_cloud_control_client_only_exposes_fixed_actions(self) -> None:
        runner = (ROOT_DEPLOY / "run-cloud-control.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("only inspect or publish is allowed", runner)
        self.assertIn("--Username beijingcodex", runner)
        self.assertIn("--ContentEncoding PlainText", runner)
        self.assertIn("--KeepCommand false", runner)
        self.assertIn("DescribeInvocationResults", runner)
        self.assertNotIn("DescribeInvocations", runner)
        self.assertIn("/usr/local/sbin/beijing-suite-inspect", runner)
        self.assertIn(
            "/usr/local/sbin/beijing-collector-publish-trigger", runner
        )
        self.assertNotIn("remote_command=\"$", runner)

    def test_cloud_control_client_checks_remote_execution_result(self) -> None:
        runner = ROOT_DEPLOY / "run-cloud-control.sh"
        with tempfile.TemporaryDirectory() as temporary:
            fake_cli = Path(temporary) / "aliyun"
            fake_cli.write_text(
                """#!/usr/bin/env python3
import json
import sys

arguments = sys.argv[1:]
if "RunCommand" in arguments:
    assert arguments[arguments.index("--Username") + 1] == "beijingcodex"
    assert arguments[arguments.index("--KeepCommand") + 1] == "false"
    command = arguments[arguments.index("--CommandContent") + 1]
    assert command == "sudo -n /usr/local/sbin/beijing-suite-inspect"
    print(json.dumps({"InvokeId": "t-test123"}))
elif "DescribeInvocationResults" in arguments:
    print(json.dumps({
        "Invocation": {
            "InvocationResults": {
                "InvocationResult": [{
                    "InvocationStatus": "Success",
                    "ExitCode": 0,
                    "Output": "inventory-safe\\n",
                }]
            }
        }
    }))
else:
    raise SystemExit(3)
""",
                encoding="utf-8",
            )
            fake_cli.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{temporary}:{environment['PATH']}"
            completed = subprocess.run(
                [str(runner), "inspect", "i-test123"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("inventory-safe", completed.stdout)
        self.assertIn("BEIJING_CLOUD_CONTROL_OK", completed.stdout)

    def test_collector_deployment_contracts_run_without_repository_root(self) -> None:
        """Match the production publisher: only the collector subtree is available."""
        collector = REPOSITORY_ROOT / "services" / "beijing-blogger-collector"
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary) / "release"
            (isolated / "tests").mkdir(parents=True)
            shutil.copy2(collector / "tests/test_beijing_deployment.py",
                         isolated / "tests/test_beijing_deployment.py")
            shutil.copytree(collector / "deploy", isolated / "deploy")
            shutil.copytree(collector / "collector_web", isolated / "collector_web")
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover",
                 "-s", "tests", "-p", "test_beijing_deployment.py"],
                cwd=isolated, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 9 tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
