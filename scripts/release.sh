#!/usr/bin/env bash
# Release a version (MISSION.md section 13): bump the single source of truth,
# check the changelog, run the full gate, build the wheel, commit, annotated
# tag, push. npm packages (when they exist) move in lockstep.
set -euo pipefail
cd "$(dirname "$0")/.."

NEW="${1:?usage: scripts/release.sh <version> (e.g. 0.2.0)}"
if [[ ! "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
  echo "release: invalid version: $NEW" >&2
  exit 1
fi
if ! grep -q "^## \[$NEW\]" CHANGELOG.md; then
  echo "release: CHANGELOG.md has no entry for $NEW" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "release: working tree is dirty; commit or stash first" >&2
  exit 1
fi

python3 - "$NEW" <<'PY'
import re
import sys

new = sys.argv[1]
with open("Cargo.toml", encoding="utf-8") as handle:
    text = handle.read()
text, count = re.subn(r'(?m)^version = "[^"]+"', f'version = "{new}"', text, count=1)
assert count == 1, "workspace version line not found"
with open("Cargo.toml", "w", encoding="utf-8") as handle:
    handle.write(text)
PY

# Lockstep npm packages (packages/ holds placeholders until 0.4.0).
for pkg in packages/*/package.json; do
  [ -e "$pkg" ] || continue
  node -e "const fs=require('fs');const p=process.argv[1];const j=JSON.parse(fs.readFileSync(p));j.version=process.argv[2];fs.writeFileSync(p,JSON.stringify(j,null,2)+'\n')" "$pkg" "$NEW"
done

cargo check --workspace >/dev/null 2>&1  # refresh Cargo.lock for the new version
just ci
uv run maturin build --release

git add Cargo.toml Cargo.lock CHANGELOG.md
for pkg in packages/*/package.json; do
  [ -e "$pkg" ] || continue
  git add "$pkg"
done
git commit -m "chore(release): v$NEW"
git tag -a "v$NEW" -m "trpc-rs-py v$NEW"
git push origin main "v$NEW"
echo "release: v$NEW tagged and pushed; wheel in target/wheels/"