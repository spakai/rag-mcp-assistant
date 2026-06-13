#!/usr/bin/env bash
# Build the Lambda deployment zip into dist/ingest.zip
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
PACKAGE_DIR="$DIST_DIR/package"
ZIP_PATH="$DIST_DIR/ingest.zip"

echo "==> Building Lambda package"
rm -rf "$PACKAGE_DIR" "$ZIP_PATH"
mkdir -p "$PACKAGE_DIR"

pip install --quiet -r "$REPO_ROOT/requirements.txt" --target "$PACKAGE_DIR"
cp -r "$REPO_ROOT/src" "$PACKAGE_DIR/"

(cd "$PACKAGE_DIR" && zip -qr "$ZIP_PATH" .)
echo "==> Built $ZIP_PATH"
