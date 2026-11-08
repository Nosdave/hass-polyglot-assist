#!/bin/sh
# Run every panel test in de/en/fr inside node:22 (node_modules live outside the repo).
set -e
cd "$(dirname "$0")/../.."
NM=${PANEL_NODE_MODULES:-/tmp/polyglot-panel-node_modules}
mkdir -p "$NM"
docker run --rm -v "$PWD":/w -v "$NM":/w/tests/panel/node_modules -w /w/tests/panel node:22-alpine sh -c '
  [ -d node_modules/happy-dom ] || npm install --no-audit --no-fund --silent >/dev/null
  rc=0; for t in test_*.mjs; do for l in de en fr; do node "$t" "$l" || rc=1; done; done; exit $rc'
