"""Absolute-path launcher for Codex and Hermes; independent of client cwd."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = os.pathsep.join([str(Path.home() / ".local/bin"), str(Path.home() / ".hermes/node/bin"), os.environ.get("PATH", "/usr/bin:/bin")])
from social_read.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("mcp")
    raise SystemExit(main())
