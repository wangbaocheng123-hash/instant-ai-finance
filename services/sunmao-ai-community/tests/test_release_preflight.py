from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/beijing/publish-via-git.sh"
BASH = shutil.which("bash")
if os.name == "nt":
    git_executable = shutil.which("git")
    candidates = (parent / "bin/bash.exe" for parent in Path(git_executable).resolve().parents) if git_executable else ()
    BASH = next((str(path) for path in candidates if path.exists()), None)


@unittest.skipUnless(BASH and shutil.which("git"), "Bash and Git required")
class PublicEntryPreflightTests(unittest.TestCase):
    def run_unavailable_entry(self, fake_curl: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="sunmao-release-preflight-") as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "--quiet", folder], check=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://invalid.example/wangbaocheng123-hash/instant-ai-finance.git"],
                cwd=folder,
                check=True,
            )
            marker = root / "network-or-ref-mutation"
            env = os.environ.copy()
            env["RELEASE_TEST_WRITES"] = marker.as_posix()
            command = r'''
git() {
    case "$1" in
        fetch|push) printf '%s\n' "$1" >>"$RELEASE_TEST_WRITES"; return 99 ;;
        *) command git "$@" ;;
    esac
}
export -f git
''' + "\ncurl() { " + fake_curl + r'''; }
export -f curl
bash "$1" "$2"
'''
            result = subprocess.run(
                [BASH, "-c", command, "preflight-test", SCRIPT.as_posix(), "0" * 40],
                cwd=folder,
                env=env,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("中文正式域名DNS或HTTPS尚未就绪", result.stderr)
            self.assertFalse(marker.exists(), result.stdout + result.stderr)
            return result

    def test_dns_failure_stops_before_fetch_or_production_push(self) -> None:
        self.run_unavailable_entry("return 6")

    def test_wrong_service_stops_before_fetch_or_production_push(self) -> None:
        self.run_unavailable_entry("printf '%s' '{\"service\":\"other-app\"}'")


if __name__ == "__main__":
    unittest.main()
