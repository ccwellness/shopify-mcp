"""Merge a `shopify-connector` entry into Claude Desktop's config.

Usage: python _merge_config.py <config_path> <install_dir> <uv_path>

Preserves any other mcpServers the user has already configured. Idempotent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EXPECTED_ARGC = 4  # prog + config_path + install_dir + uv_path


def main(argv: list[str]) -> int:
    if len(argv) != _EXPECTED_ARGC:
        print(f"usage: {argv[0]} <config_path> <install_dir> <uv_path>", file=sys.stderr)
        return 2

    config_path = Path(argv[1])
    install_dir = argv[2]
    uv_path = argv[3]

    config: dict = {}
    if config_path.exists() and config_path.stat().st_size > 0:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"existing config is not valid JSON: {e}", file=sys.stderr)
            return 1

    servers = config.setdefault("mcpServers", {})
    servers["shopify-connector"] = {
        "command": uv_path,
        "args": ["run", "python", "-m", "mcp_server"],
        "cwd": install_dir,
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
