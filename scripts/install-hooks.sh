#!/usr/bin/env bash
# Install the git hooks (pre-commit: ci-fast, pre-push: full ci).
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push
echo "hooks installed: pre-commit -> just ci-fast, pre-push -> just ci"
