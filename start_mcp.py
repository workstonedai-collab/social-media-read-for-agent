"""Absolute-path launcher for Codex and Hermes; independent of client cwd."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = os.pathsep.join([str(ROOT / ".state/opencli/node_modules/.bin"), str(Path.home() / ".local/bin"), str(Path.home() / ".hermes/node/bin"), os.environ.get("PATH", "/usr/bin:/bin")])

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("mcp")
    if "--worker" in sys.argv:
        sys.argv.remove("--worker")
        from social_read.cli import main
        raise SystemExit(main())
    if sys.argv[-1] == "mcp":
        from social_read.gateway import run
        run(ROOT, sys.argv[1:])
    else:
        from social_read.cli import main
        raise SystemExit(main())
