#!/usr/bin/env bash
# Build the Lambda zip, and the static site beside it. No Docker required.
#
# The package list is DERIVED from requirements.txt. It used to be restated
# here, and the two drifted: the web-push chain was added to this file and never
# to the manifest, so a clean checkout could not import the app while this build
# kept passing. Adding a dependency in one place is now enough.
#
# pyswisseph is a C extension, so the package has to carry Linux binaries. It
# publishes manylinux wheels up to cp311 and no further, which is why the
# runtime is pinned to Python 3.11. Bump it only when upstream ships cp312.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=deploy/build
rm -rf "$OUT" && mkdir -p "$OUT/pkg"

PIP="python3 -m pip install --quiet --target $OUT/pkg"
PLAT="--only-binary=:all: --platform manylinux_2_17_x86_64 --python-version 3.11 --implementation cp --abi cp311"

# --- exceptions, each with a reason -----------------------------------------
# Only for the Test 2 SMS rig, which never runs on Lambda.
NOT_IN_LAMBDA="twilio"
# pywebpush requires http-ece, which publishes no wheel. Under --only-binary the
# resolver backtracks through every pywebpush version hunting for a combination
# that cannot exist — 16+ minutes before failing. Naming these explicitly with
# --no-deps means the resolver never sees the impossible constraint.
NO_RESOLVE="pywebpush py-vapid cryptography cffi pycparser bcrypt"
# Pure Python — verified: no compiled artifacts, one module. The platform pins
# exist to stop C extensions being built for the wrong architecture, and there
# is no C here.
PURE_SDIST="http-ece"

names() { sed -E 's/#.*//; s/[[:space:]]//g; s/[<>=!~].*//' requirements.txt | grep -v '^$'; }

exclude="$(echo "$NOT_IN_LAMBDA $NO_RESOLVE $PURE_SDIST" | tr ' ' '\n' | grep -v '^$')"
WHEELS="$(names | grep -vxF "$exclude" | tr '\n' ' ')"

echo "→ deps from requirements.txt (linux/x86_64, cp311)"
echo "   $WHEELS"
# shellcheck disable=SC2086
$PIP $PLAT $WHEELS

echo "→ push chain (pinned, no resolution)"
# shellcheck disable=SC2086
$PIP $PLAT --no-deps $NO_RESOLVE

echo "→ $PURE_SDIST (pure python, sdist)"
# shellcheck disable=SC2086
$PIP --no-deps $PURE_SDIST
if find "$OUT/pkg/http_ece" -name '*.so' 2>/dev/null | grep -q .; then
  echo "http-ece shipped a compiled artifact — stop and rebuild it for linux" >&2
  exit 1
fi

# boto3 ships in the Lambda runtime; carrying our own just inflates the zip
rm -rf "$OUT/pkg"/boto3* "$OUT/pkg"/botocore* "$OUT/pkg"/s3transfer*

echo "→ source"
cp -r app engine prompts "$OUT/pkg/"
# The website, for / with staticFromS3 off (app/server.py serves it then).
# Only what a visitor may fetch: the same files serve.py keeps private stay out.
if [ -d site ]; then
  mkdir -p "$OUT/pkg/site"
  cp -R site/. "$OUT/pkg/site/"
  rm -rf "$OUT/pkg/site/serve.py" "$OUT/pkg/site/README.md" "$OUT/pkg/site/og.html" "$OUT/pkg/site/__pycache__"
fi
find "$OUT/pkg" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
# Strip the local JSON store, which is only ever at the package root. This used
# to be `-name data`, which also deleted app/data/ — the bundled city dataset —
# so every signup on Lambda would have crashed on a missing file.
rm -rf "$OUT/pkg/data"

# The Lambda Web Adapter layer execs this to start the server. No access log:
# app/log.py already writes one line per request, by route template, and
# uvicorn's carried the client IP and the raw query (what she typed to find
# her birthplace) into CloudWatch.
cat > "$OUT/pkg/run.sh" <<'SH'
#!/bin/bash
exec python -m uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
SH
chmod +x "$OUT/pkg/run.sh"

# Declared-only verification is not enough: it passed a build that was missing
# cffi, because cffi is a transitive of cryptography rather than a line in
# requirements.txt. Walk what actually shipped and check each package's own
# stated requirements are present too.
echo "→ verifying the dependency closure"
python3 - "$OUT/pkg" <<'CLOSURE'
import glob, os, pathlib, re, sys
pkg = sys.argv[1]
present, missing = set(), []
for meta in glob.glob(os.path.join(pkg, "*.dist-info", "METADATA")):
    present.add(pathlib.Path(meta).parent.name.split("-")[0].lower().replace("_", "-"))
for meta in glob.glob(os.path.join(pkg, "*.dist-info", "METADATA")):
    owner = pathlib.Path(meta).parent.name.split("-")[0]
    for line in open(meta, encoding="utf-8", errors="ignore"):
        if not line.startswith("Requires-Dist:"):
            continue
        req = line.split(":", 1)[1].strip()
        if ";" in req:                       # skip extras and env markers
            continue
        name = re.split(r"[<>=!\[ ]", req)[0].strip().lower().replace("_", "-")
        if name and name not in present and name != "boto3":
            missing.append(f"{name} (needed by {owner})")
if missing:
    print("  missing from the package:", *sorted(set(missing)), sep="\n    ")
    sys.exit(1)
print(f"  closure clean — {len(present)} packages, every stated requirement present")
CLOSURE

echo "→ verifying every declared dependency made it in"
# Match on dist-info rather than guessing the import name: pyswisseph installs
# `swisseph`, python-multipart installs `multipart`, http-ece installs
# `http_ece`. The dist-info directory is the only thing named after the package.
missing=""
for pkg in $(names); do
  case " $NOT_IN_LAMBDA boto3 " in *" $pkg "*) continue;; esac   # boto3: runtime provides it
  norm="$(echo "$pkg" | tr 'A-Z-' 'a-z_')"
  if ! compgen -G "$OUT/pkg/${norm}-*.dist-info" >/dev/null; then
    missing="$missing $pkg"
  fi
done
if [ -n "$missing" ]; then
  echo "declared in requirements.txt but absent from the package:$missing" >&2
  exit 1
fi
echo "   all $(names | wc -l | tr -d ' ') declared packages accounted for"

# The city dataset is the one asset the app cannot start without.
if [ ! -f "$OUT/pkg/app/data/cities.tsv.gz" ]; then
  echo "app/data/cities.tsv.gz missing from the package — birthplace lookup would 500" >&2
  exit 1
fi
# Ship the search index built, so a cold start never spends seconds making it.
echo "→ city index"
rm -f "$OUT/pkg/app/data/cities.sqlite"
python3 -c "import sys; sys.path.insert(0, '$OUT/pkg'); from app import cities; \
  cities.build_db(); print('  ', cities.count(), 'places')" | tail -1
[ -f "$OUT/pkg/app/data/cities.sqlite" ] || { echo "city index failed to build" >&2; exit 1; }
# The index is the dataset from here on. Shipping the 5MB source as well only
# gave the runtime a way to decide the index was stale and rebuild it in /tmp.
rm -f "$OUT/pkg/app/data/cities.tsv.gz"

# The ephemeris files and the NASA reference ship with the engine. Without the
# files the engine still runs — on the low-precision fallback, with no Chiron —
# while /accuracy says full precision. That is a lie the build refuses to ship.
for f in engine/ephe/sepl_18.se1 engine/ephe/semo_18.se1 engine/ephe/seas_18.se1 \
         engine/reference/horizons.json; do
  if [ ! -s "$OUT/pkg/$f" ]; then
    echo "$f missing from the package — positions would silently drop precision" >&2
    exit 1
  fi
done

# --- cold start ---------------------------------------------------------------
# /var/task is read-only, so without bytecode in the zip every cold start
# compiled every module from source and threw the result away: seconds of a
# ~5s init. unchecked-hash because Lambda does not promise to keep the mtimes a
# timestamp .pyc is validated against; the zip is immutable, so nothing to check.
# The magic number must match the runtime, so this needs a 3.11 interpreter —
# and a runtime bump must bump it too: bytecode without source will not load on
# another version at all.
#
# Dependencies ship as bytecode only (-b puts foo.pyc where foo.py was, which is
# the one layout Python imports without the source), so the zip shrinks rather
# than carrying both. Our own code keeps its source: tracebacks in the logs
# need the lines, and pydantic/fastapi/anthropic introspect our functions.
echo "→ bytecode (cp311)"
PY311=""
for c in python3.11 python3 python; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' 2>/dev/null; then
    PY311="$c"; break
  fi
done
[ -n "$PY311" ] || PY311="$(uv python find 3.11 2>/dev/null || true)"
[ -n "$PY311" ] || { echo "no Python 3.11 to compile bytecode with — the runtime would compile on every cold start" >&2; exit 1; }
# Third-party test suites and type stubs never run in Lambda.
find "$OUT/pkg" -mindepth 2 -maxdepth 2 -type d -name tests -exec rm -rf {} +
find "$OUT/pkg" -name '*.pyi' -delete
find "$OUT/pkg" -name '__pycache__' -type d -exec rm -rf {} +    # the city-index step's
# Paths as Lambda will see them, for the tracebacks of code that has no source.
PKG="$PWD/$OUT/pkg"
PYC=("$PY311" -m compileall -q -f -j 0 --invalidation-mode unchecked-hash -s "$PKG" -p /var/task)
OURS="^$PKG/(app|engine)(/|$)"
# shellcheck disable=SC2046
"${PYC[@]}" -b $(find "$PKG" -mindepth 1 -maxdepth 1 \( -type d -o -name '*.py' \) | grep -Ev "$OURS") >/dev/null
find "$PKG" -name '*.py' | grep -Ev "$OURS" | while read -r f; do
  if [ -f "${f}c" ]; then rm "$f"; fi
done
"${PYC[@]}" "$PKG/app" "$PKG/engine" >/dev/null
echo "   $(find "$OUT/pkg" -name '*.pyc' | wc -l | tr -d ' ') modules compiled with $("$PY311" -V)"
# ------------------------------------------------------------------------------

echo "→ zip"
(cd "$OUT/pkg" && zip -qr ../mysign.zip .)
echo "built $OUT/mysign.zip  ($(du -h "$OUT/mysign.zip" | cut -f1))"

# The files CloudFront serves from S3 when -c staticFromS3 is on (the zip
# still carries them all, for local dev and the flag-off path). The marketing
# site in site/, when it exists, supplies the root; the app's own paths are
# copied over it, so a stray file there can never replace the worker or the app.
echo "→ site"
SITE="$OUT/site"
mkdir -p "$SITE"
if [ -d site ]; then
  cp -R site/. "$SITE/"
  rm -rf "$SITE/serve.py" "$SITE/README.md" "$SITE/og.html" "$SITE/__pycache__"     # tooling, not pages
else
  cp app/static/index.html "$SITE/index.html"     # no website yet: / stays the app
fi
mkdir -p "$SITE/static" "$SITE/app"
cp -R app/static/. "$SITE/static/"
cp app/static/index.html "$SITE/app/index.html"
cp app/static/sw.js app/static/manifest.webmanifest "$SITE/"
for page in privacy terms refunds support cookies accuracy; do cp "app/static/$page.html" "$SITE/$page.html"; done
cp app/static/icons/icon-192.png "$SITE/favicon.ico"
for f in index.html app/index.html sw.js manifest.webmanifest privacy.html terms.html refunds.html support.html cookies.html \
         accuracy.html favicon.ico static/icons/icon-192.png \
         $([ -d site ] && echo brand.css sky.js site.js track.js consent.js fonts/fonts.css og.png robots.txt sitemap.xml ig.html check.html gifts/index.html gifts/scorpio.html); do
  [ -s "$SITE/$f" ] || { echo "site/$f missing from the build" >&2; exit 1; }
done
echo "built $SITE  ($(find "$SITE" -type f | wc -l | tr -d ' ') files)"
