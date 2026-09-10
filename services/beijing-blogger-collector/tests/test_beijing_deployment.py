from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "beijing"
REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
ROOT_DEPLOY = REPOSITORY_ROOT / "deploy" / "beijing"


class BeijingDeploymentContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DEPLOY_ROOT / name).read_text(encoding="utf-8")

    def test_git_publisher_is_fast_forward_only_and_never_cleans_runtime_data(self) -> None:
        script = self.read("blogger-collector-git-deploy")
        self.assertIn("set -Eeuo pipefail", script)
        self.assertIn("merge-base --is-ancestor", script)
        self.assertIn("refs/heads/beijing-production", script)
        self.assertIn('target_tree="$(git_read rev-parse', script)
        self.assertIn("known failed revision skipped", script)
        self.assertIn("flock -n", script)
        self.assertIn("-m unittest discover", script)
        self.assertIn("PrivateNetwork=yes", script)
        self.assertIn('BUILD_USER="bloggerbuild"', script)
        self.assertIn('GIT_USER="bloggergit"', script)
        self.assertIn("http://127.0.0.1:18797/health", script)
        self.assertIn("http://127.0.0.1:18797/api/collector/status", script)
        self.assertIn('chmod -R u=rwX,go=rX "${stage_dir}"', script)
        self.assertNotIn("reset --hard", script)
        self.assertNotIn("git clean", script)
        self.assertNotIn("rm -rf -- /var/lib", script)
        self.assertNotIn("source_dir}/deploy/", script)

    def test_runtime_uses_the_atomically_selected_release(self) -> None:
        service = self.read("blogger-collector.service")
        self.assertIn(
            "ExecStart=/usr/bin/env BLOGGER_AGENT_ENV_FILE=/dev/null "
            "/opt/blogger-agent/current/.venv/bin/python ",
            service,
        )
        self.assertIn("ReadWritePaths=/var/lib/blogger-agent", service)
        self.assertIn("MemoryMax=1600M", service)

    def test_git_reader_uses_public_https_without_private_credentials(self) -> None:
        script = self.read("blogger-collector-git-deploy")
        service = self.read("blogger-collector-git-deploy.service")
        self.assertIn("https://github.com/wangbaocheng123-hash/instant-ai-finance.git", script)
        self.assertNotIn("deploy_key", script)
        self.assertNotIn("GIT_SSH_COMMAND", service)
        self.assertIn("ProtectHome=true", service)

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

    def test_collection_suite_manifest_keeps_transition_fail_closed(self) -> None:
        manifest = json.loads(
            (ROOT_DEPLOY / "collection-suite.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["production_branch"], "beijing-production")
        self.assertEqual(manifest["deployment"]["mode"], "transition")
        self.assertFalse(manifest["deployment"]["timer_enabled"])
        components = {
            item["component_id"]: item for item in manifest["components"]
        }
        self.assertEqual(
            components["blogger-collector"]["source_state"], "managed"
        )
        self.assertEqual(
            components["model-downloader"]["source_state"],
            "pending_verified_import",
        )
        self.assertFalse(manifest["integration"]["shared_process"])
        self.assertFalse(manifest["integration"]["shared_database"])

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

    def test_release_gate_runs_complete_isolated_suite_with_test_floor(self) -> None:
        script = self.read("blogger-collector-git-deploy")
        self.assertIn("https://mirrors.aliyun.com/pypi/simple/", script)
        self.assertIn("-p 'test_*.py'", script)
        self.assertIn('"${count}" -ge 203', script)
        self.assertIn("MODEL_DOWNLOADER_BRIDGE_ENABLED=0", script)
        self.assertNotIn('BLOGGER_AGENT_MEDIA_DIR="${source_dir}', script)

    def test_recurring_timer_is_retired(self) -> None:
        self.assertFalse((DEPLOY_ROOT / "blogger-collector-git-deploy.timer").exists())

    def test_installer_only_adds_the_fixed_channel(self) -> None:
        installer = self.read("install-git-deploy-channel.sh")
        self.assertIn('/usr/local/sbin/blogger-collector-git-deploy', installer)
        self.assertIn("root:root:755", installer)
        self.assertIn("bloggergit", installer)
        self.assertIn("bloggerbuild", installer)
        self.assertIn("disable --now blogger-collector-git-deploy.timer", installer)
        self.assertNotIn("enable --now blogger-collector-git-deploy.timer", installer)
        self.assertIn("--replace-managed-files", installer)
        self.assertIn("/var/cache/blogger-agent-pip", installer)
        self.assertNotIn("collector.env", installer)
        self.assertNotIn("model-downloader", installer)
        self.assertNotIn("caddy", installer.lower())
        self.assertNotIn("rm -rf -- /var/lib", installer)

    def test_mobile_page_keeps_device_width_contract(self) -> None:
        collector_html = (PROJECT_ROOT / "collector_web" / "index.html").read_text(
            encoding="utf-8"
        )
        collector_css = (PROJECT_ROOT / "collector_web" / "styles.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('name="viewport"', collector_html)
        self.assertIn("width=device-width", collector_html)
        self.assertIn("仅手动采集", collector_html)
        self.assertIn('id="addCreatorButton"', collector_html)
        self.assertIn('id="settingsHistoryLimit"', collector_html)
        self.assertIn('id="settingsCommentLimit"', collector_html)
        self.assertIn('id="settingsTrackingHours"', collector_html)
        self.assertIn('id="saveAndRunButton"', collector_html)
        self.assertIn('id="settingsVideoUrl"', collector_html)
        self.assertIn('id="runSingleVideoButton"', collector_html)
        self.assertIn('aria-live="polite"', collector_html)
        self.assertIn('href="/model"', collector_html)
        self.assertIn("@media (max-width: 620px)", collector_css)

        manifest = (
            PROJECT_ROOT / "collector_web" / "manifest.webmanifest"
        ).read_text(encoding="utf-8")
        service_worker = (
            PROJECT_ROOT / "collector_web" / "service-worker.js"
        ).read_text(encoding="utf-8")
        self.assertIn('"display": "standalone"', manifest)
        self.assertIn('"short_name": "北极采集"', manifest)
        self.assertIn('url.pathname.includes("/api/collector/")', service_worker)
        self.assertIn("SCOPE_PATH", service_worker)

    def test_unified_domain_reuses_model_login_and_keeps_both_backends_loopback(self) -> None:
        caddy = self.read("collector.Caddyfile.example")
        self.assertIn("collector.amuyeye.com", caddy)
        self.assertIn("forward_auth 127.0.0.1:8787", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:18797", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:8787", caddy)
        self.assertIn("handle_path /collector/*", caddy)
        self.assertIn("@collector_without_slash path /collector", caddy)
        self.assertIn("redir @collector_without_slash /collector/ 308", caddy)
        self.assertIn("@legacy_hub path /hub /hub/", caddy)
        self.assertIn("redir @legacy_hub / 308", caddy)
        self.assertIn("@model_entry path /model /model/", caddy)
        self.assertIn("@suite_home path /", caddy)
        self.assertIn("rewrite * /hub", caddy)
        self.assertIn("handle /login*", caddy)
        self.assertNotIn("handle /hub*", caddy)
        self.assertIn("Preserve every existing absolute model-downloader route", caddy)
        self.assertNotIn("basic_auth", caddy)
        self.assertNotIn("collector-access.caddy", caddy)

    def test_collector_caddy_apply_script_is_scoped_and_recoverable(self) -> None:
        script = self.read("apply-collector-caddy.sh")
        self.assertIn("collector.amuyeye.com {", script)
        self.assertIn('TEMPLATE="${SOURCE_DIR}/collector.Caddyfile.example"', script)
        self.assertNotIn("/opt/blogger-agent/repository", script)
        self.assertIn("site_block_count", script)
        self.assertIn("caddy validate --adapter caddyfile", script)
        self.assertIn("restore_previous_config", script)
        self.assertIn("systemctl reload caddy.service", script)
        self.assertNotIn("systemctl restart blogger-collector", script)


if __name__ == "__main__":
    unittest.main()
