from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "beijing"


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

    def test_publisher_falls_back_to_verified_github_subtree_archive(self) -> None:
        publisher = self.read("sunmao-community-git-deploy")
        self.assertIn('GITHUB_API_ROOT="https://api.github.com/repos/wangbaocheng123-hash/instant-ai-finance"', publisher)
        self.assertIn('GITHUB_CODELOAD_ROOT="https://codeload.github.com/wangbaocheng123-hash/instant-ai-finance/tar.gz"', publisher)
        self.assertIn("timeout --signal=TERM 45s", publisher)
        self.assertIn('source_mode="codeload"', publisher)
        self.assertIn('subtree = ("services", "sunmao-ai-community")', publisher)
        self.assertIn('compare/${accepted_commit}...${target_commit}', publisher)
        self.assertIn('github_tree_entry_sha "${subtree_payload}" sunmao-ai-community', publisher)

    def test_caddy_proxies_directly_to_the_peer_service(self) -> None:
        caddy = self.read("sunmao-community.Caddyfile.example")
        apply_script = self.read("apply-caddy.sh")
        self.assertIn("xn--4pv19va7008a.com {", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:18808", caddy)
        self.assertNotIn("*.amuyeye.com", caddy)
        self.assertNotIn("mcp.amuyeye.com {", caddy)
        self.assertNotIn("rewrite * /community", caddy)
        self.assertNotIn("18797", caddy)
        self.assertIn("restore_previous_config", apply_script)
        self.assertIn("http://127.0.0.1:18808/health", apply_script)
        self.assertNotIn("systemctl restart blogger-collector", apply_script)

    def test_singapore_root_proxy_is_exact_and_uses_the_secure_tunnel(self) -> None:
        singapore_root = PROJECT_ROOT / "deploy" / "singapore"
        caddy = (singapore_root / "sunmao-community-root.Caddyfile.example").read_text(
            encoding="utf-8"
        )
        tunnel = (
            singapore_root / "sunmao-community-beijing-tunnel.service"
        ).read_text(encoding="utf-8")
        self.assertIn("amuyeye.com {", caddy)
        self.assertIn("reverse_proxy 127.0.0.1:18818", caddy)
        self.assertIn("path /service-worker.js", caddy)
        self.assertIn("skipWaiting", caddy)
        self.assertNotIn("mcp.amuyeye.com {", caddy)
        self.assertNotIn("*.amuyeye.com", caddy)
        self.assertIn("User=compassdev", tunnel)
        self.assertIn("ExitOnForwardFailure=yes", tunnel)
        self.assertIn("127.0.0.1:18818:127.0.0.1:18808", tunnel)
        self.assertIn("singaporecodex@47.93.214.76", tunnel)
        self.assertNotIn("GatewayPorts=yes", tunnel)

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

    def test_rejoin_script_restores_the_monorepo_without_touching_peer_data(self) -> None:
        migration = self.read("rejoin-original-repository.sh")
        self.assertIn(
            "https://github.com/wangbaocheng123-hash/instant-ai-finance.git",
            migration,
        )
        self.assertIn(
            "https://github.com/wangbaocheng123-hash/sunmao-ai-community.git",
            migration,
        )
        self.assertIn("migration-backups", migration)
        self.assertLess(
            migration.index('mv -- "${BARE_REPOSITORY}"'),
            migration.index("repository_moved=true"),
        )
        self.assertIn("sunmao-community-deploy-trigger.path", migration)
        self.assertIn("disable --now sunmao-community-git-deploy.timer", migration)
        self.assertIn("blogger-collector.service", migration)
        self.assertNotIn("systemctl restart blogger-collector", migration)
        self.assertNotIn("/var/lib/blogger-agent", migration)
        self.assertNotIn("community.sqlite3", migration)
        self.assertNotIn("admin-username", migration)
        self.assertNotIn("admin-password.hash", migration)


if __name__ == "__main__":
    unittest.main()
