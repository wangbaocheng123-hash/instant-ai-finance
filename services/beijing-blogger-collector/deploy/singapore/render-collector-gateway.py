"""Render the Singapore ingress from the Beijing suite's reviewed routes."""

import sys
from pathlib import Path


def render() -> str:
    template = Path(__file__).resolve().parents[1] / "beijing" / "collector.Caddyfile.example"
    site = template.read_text(encoding="utf-8")
    upstreams = {
        "127.0.0.1:18797": "127.0.0.1:18817",
        "127.0.0.1:8787": "127.0.0.1:18819",
    }
    if "collector.grandpaamu.com {" not in site or any(host not in site for host in upstreams):
        raise ValueError("Collector site or upstreams changed; review the gateway before rendering.")
    for beijing, singapore in upstreams.items():
        site = site.replace(beijing, singapore)
    return site


if __name__ == "__main__":
    sys.stdout.buffer.write(render().encode("utf-8"))
