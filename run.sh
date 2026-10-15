#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$ROOT/.state/runtime/python/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
[ -n "$PYTHON" ] || { echo '请由 Agent 先执行 /bin/bash install.sh。' >&2; exit 2; }
export PATH="$ROOT/.state/runtime/node/bin:$ROOT/.state/opencli/node_modules/.bin:${PATH:-/usr/bin:/bin}"
if [ "${1:-}" = setup ]; then shift; exec "$PYTHON" "$ROOT/bootstrap.py" "$@"; fi
if [ "${1:-}" = demo ]; then shift; exec "$PYTHON" "$ROOT/scripts/demo_research.py" "$@"; fi
if [ "${1:-}" = stability ]; then shift; exec "$PYTHON" "$ROOT/scripts/verify_live_stability.py" "$@"; fi
exec "$PYTHON" "$ROOT/start_mcp.py" "$@"
