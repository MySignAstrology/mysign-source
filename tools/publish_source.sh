#!/usr/bin/env bash
# Publish the source of a deployed commit to the public AGPL repository.
#
# AGPL section 13: people who use My Sign over a network must be offered the
# complete source of the version they are using. Run this after every
# production deploy, with the commit that was deployed (default: HEAD).
#
# Only a snapshot of the tree is published, one public commit per deploy. The
# private history (old branches, author emails, pull requests) stays private.
#
# Usage: tools/publish_source.sh [commit]
set -euo pipefail
PUBLIC="${MYSIGN_SOURCE_REPO:-git@github.com:MySignAstrology/mysign-source.git}"
REV="$(git rev-parse "${1:-HEAD}")"
SHORT="$(git rev-parse --short "$REV")"
cd "$(git rev-parse --show-toplevel)"
for f in LICENSE NOTICE.md; do
  git cat-file -e "$REV:$f" || { echo "$f is missing at $SHORT; refusing to publish" >&2; exit 1; }
done

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
git clone -q --depth 1 "$PUBLIC" "$work/pub" 2>/dev/null || { mkdir -p "$work/pub"; git -C "$work/pub" init -q -b main; git -C "$work/pub" remote add origin "$PUBLIC"; }
find "$work/pub" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git archive "$REV" | tar -x -C "$work/pub"
cd "$work/pub"
git add -A
if git diff --cached --quiet; then echo "public source already matches $SHORT"; exit 0; fi
GIT_AUTHOR_NAME="XII Studios" GIT_AUTHOR_EMAIL="hello@mysignapp.com" \
GIT_COMMITTER_NAME="XII Studios" GIT_COMMITTER_EMAIL="hello@mysignapp.com" \
  git commit -q -m "Source of My Sign as deployed ($SHORT, $(date -u +%Y-%m-%d))"
git push -q origin HEAD:main
echo "published $SHORT to $PUBLIC"
