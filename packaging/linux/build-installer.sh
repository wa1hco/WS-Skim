#!/usr/bin/env bash
# build-installer.sh — build dist/ws-skim-linux-install.sh, a single
# self-extracting installer (shell header + tar.gz of the staged tree).
#
#     packaging/linux/build-installer.sh [--tag v0.1.4-test]
#
# The output name has no version in it so the README can link to
# releases/latest/download/ws-skim-linux-install.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

TAG_ARGS=()
if [ "${1:-}" = "--tag" ]; then TAG_ARGS=(--tag "$2"); fi

STAGE="$ROOT/build/stage-linux"
VERSION="$(python3 tools/dev/stage_release.py --out "$STAGE" ${TAG_ARGS[@]+"${TAG_ARGS[@]}"})"

mkdir -p dist
OUT="dist/ws-skim-linux-install.sh"
sed "s/@VERSION@/$VERSION/g" packaging/linux/installer-header.sh > "$OUT"
tar -czf - -C "$STAGE" --owner=0 --group=0 . >> "$OUT"
chmod 755 "$OUT"
echo "Built $OUT (WS Skim $VERSION, $(du -h "$OUT" | cut -f1))"
