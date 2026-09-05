from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "beijing"


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

    def test_release_gate_runs_complete_isolated_suite_with_test_floor(self) -> None:
        script = self.read("blogger-collector-git-deploy")
        self.assertIn("https://mirrors.aliyun.com/pypi/simple/", script)
        self.assertIn("-p 'test_*.py'", script)
        self.assertIn('"${count}" -ge 203', script)
        self.assertIn("MODEL_DOWNLOADER_BRIDGE_ENABLED=0", script)

    def test_timer_checks_code_at_the_approved_interval(self) -> None:
        timer = self.read("blogger-collector-git-deploy.timer")
        self.assertIn("OnUnitActiveSec=90s", timer)
        self.assertIn("RandomizedDelaySec=5s", timer)
        self.assertIn("blogger-collector-git-deploy.service", timer)

    def test_installer_only_adds_the_fixed_channel(self) -> None:
        installer = self.read("install-git-deploy-channel.sh")
        self.assertIn('/usr/local/sbin/blogger-collector-git-deploy', installer)
        self.assertIn("root:root:755", installer)
        self.assertIn("bloggergit", installer)
        self.assertIn("bloggerbuild", installer)
        self.assertIn("enable --now blogger-collector-git-deploy.timer", installer)
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
        self.assertIn("site_block_count", script)
        self.assertIn("caddy validate --adapter caddyfile", script)
        self.assertIn("restore_previous_config", script)
        self.assertIn("systemctl reload caddy.service", script)
        self.assertNotIn("systemctl restart blogger-collector", script)


if __name__ == "__main__":
    unittest.main()
