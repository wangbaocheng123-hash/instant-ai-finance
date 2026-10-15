from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "beijing"
SINGAPORE_DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "singapore"


class IndependentDeploymentContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (DEPLOY_ROOT / name).read_text(encoding="utf-8")

    def test_service_has_its_own_identity_port_and_data_root(self) -> None:
        service = self.read("sunmao-ai-community.service")
        self.assertIn("User=sunmaocommunity", service)
        self.assertIn("127.0.0.1 --port 18808", service)
        self.assertIn("/var/lib/sunmao-community/community.sqlite3", service)
        self.assertIn("/opt/sunmao-community/current", service)
        self.assertNotIn("bloggeragent", service)
        self.assertNotIn("18797", service)

    def test_publisher_uses_only_the_community_branch_and_subtree(self) -> None:
        publisher = self.read("sunmao-community-git-deploy")
        self.assertIn("refs/remotes/origin/sunmao-production", publisher)
        self.assertIn('SUBTREE="services/sunmao-ai-community"', publisher)
        self.assertIn('SERVICE_NAME="sunmao-ai-community.service"', publisher)
        self.assertIn("merge-base --is-ancestor", publisher)
        self.assertIn("flock -n", publisher)
        self.assertIn("validate_release_path", publisher)
        self.assertIn("release marker mismatch", publisher)
        self.assertNotIn("beijing-production", publisher)
        self.assertNotIn("systemctl restart blogger-collector", publisher)
        self.assertNotIn("/var/lib/blogger-agent", publisher)

    def test_publisher_does_not_reference_a_local_in_its_declaration(self) -> None:
        publisher = self.read("sunmao-community-git-deploy")
        self.assertIn('local source_dir="$1"\n', publisher)
        self.assertIn('local output_file="${source_dir}/.test-output" count', publisher)
        self.assertNotIn(
            'local source_dir="$1" output_file="${source_dir}/.test-output"',
            publisher,
        )

    def test_caddy_proxies_directly_to_the_peer_service(self) -> None:
        caddy = self.read("sunmao-community.Caddyfile.example")
        apply_script = self.read("apply-caddy.sh")
        self.assertIn("reverse_proxy 127.0.0.1:18808", caddy)
        self.assertNotIn("rewrite * /community", caddy)
        self.assertNotIn("18797", caddy)
        self.assertIn("restore_previous_config", apply_script)
        self.assertIn("http://127.0.0.1:18808/health", apply_script)
        self.assertNotIn("systemctl restart blogger-collector", apply_script)

    def test_release_trigger_is_independent(self) -> None:
        script = self.read("publish-via-git.sh")
        self.assertIn("refs/heads/sunmao-production", script)
        self.assertIn("sunmao-community-git-deploy.service", script)
        self.assertNotIn("refs/heads/beijing-production", script)
        self.assertNotIn("blogger-collector-git-deploy.service", script)

    def test_installer_keeps_collector_running_and_adds_no_timer(self) -> None:
        installer = self.read("install-independent-service.sh")
        self.assertIn("blogger-collector.service", installer)
        self.assertIn("collector must remain healthy", installer)
        self.assertIn("disable --now sunmao-community-git-deploy.timer", installer)
        self.assertIn("enable sunmao-ai-community.service", installer)
        self.assertNotIn("systemctl stop blogger-collector", installer)
        self.assertNotIn("systemctl restart blogger-collector", installer)


class SingaporeDeploymentContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (SINGAPORE_DEPLOY_ROOT / name).read_text(encoding="utf-8")

    def test_caddy_claims_only_the_two_exact_community_hosts(self) -> None:
        caddy = self.read("sunmao-community.Caddyfile.example")
        self.assertIn("amuyeye.com, xn--4pv19va7008a.com {", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:18808", caddy)
        self.assertNotIn("mcp.amuyeye.com", caddy)
        self.assertNotIn("*.amuyeye.com", caddy)
        self.assertNotIn("www.amuyeye.com", caddy)

    def test_caddy_cutover_is_additive_and_guards_peer_sites(self) -> None:
        script = self.read("apply-caddy.sh")
        self.assertIn('SITE_TARGET="/etc/caddy/sunmao-community.caddy"', script)
        self.assertIn('WRAPPER_TARGET="/etc/caddy/Caddyfile.instant-ai"', script)
        self.assertIn("Time Compass still claims amuyeye.com", script)
        self.assertIn("https://mcp.amuyeye.com/health", script)
        self.assertIn("https://mcp.amuyeye.com/mcp", script)
        self.assertIn("https://compass.grandpaamu.com/", script)
        self.assertIn("restore_previous_config", script)
        self.assertNotIn('TARGET="/etc/caddy/Caddyfile"', script)
        self.assertNotIn("TIME_COMPASS_MCP_DOMAIN=", script)
        self.assertNotIn("systemctl restart time-compass", script)
        self.assertNotIn("systemctl restart instant-ai", script)

    def test_installer_grants_only_the_fixed_community_publisher(self) -> None:
        installer = self.read("install-independent-service.sh")
        sudoers = self.read("compassdev-sunmao-community-publish.sudoers")
        self.assertEqual(
            sudoers.strip(),
            "compassdev ALL=(root) NOPASSWD: /usr/local/sbin/sunmao-community-git-deploy",
        )
        self.assertIn("visudo -cf", installer)
        self.assertIn("disable --now sunmao-community-git-deploy.timer", installer)
        self.assertIn("instant-ai.service", installer)
        self.assertIn("time-compass.service", installer)
        self.assertNotIn("systemctl restart instant-ai", installer)
        self.assertNotIn("systemctl restart time-compass", installer)

    def test_cutover_verification_keeps_mcp_auth_boundary(self) -> None:
        script = self.read("verify-cutover.sh")
        self.assertIn("https://mcp.amuyeye.com/health", script)
        self.assertIn("https://mcp.amuyeye.com/mcp", script)
        self.assertIn("WWW-Authenticate:.*resource_metadata", script)
        self.assertIn("https://grandpaamu.com/api/health", script)
        self.assertIn("https://compass.grandpaamu.com/", script)


if __name__ == "__main__":
    unittest.main()
