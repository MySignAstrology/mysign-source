"""
How long `import app.server` takes in a fresh interpreter, and what it spends it on.

    python tools/import_profile.py              # best/median of 5, cached bytecode
    python tools/import_profile.py --no-pyc     # compile every module from source
    python tools/import_profile.py --top 25     # plus the heaviest imports (-X importtime)

--no-pyc is the Lambda case when the zip ships without bytecode: /var/task is
read-only, so every cold start compiles every module again and nothing is kept.
The store is pointed at DynamoDB (no call is made at import) so the import graph
matches production; secrets are not loaded, so no network is involved.
"""
import argparse
import os
import pathlib
import shutil
import statistics
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBE = ("import time, sys; t = time.perf_counter(); import app.server; "
         "sys.stderr.write(f'@@{time.perf_counter() - t}\\n')")


def _env(no_pyc, cache):
    env = {**os.environ, "MYSIGN_TABLE": "import-profile", "AWS_DEFAULT_REGION": "us-east-2",
           "PYTHONPATH": str(ROOT)}
    env.pop("MYSIGN_SECRET_ARN", None)
    if no_pyc:
        # An empty cache prefix that is never written to: every import compiles.
        env["PYTHONPYCACHEPREFIX"] = str(cache)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(args, env):
    r = subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                       capture_output=True, text=True, check=True)
    return r.stderr


def timings(runs, no_pyc, cache):
    if not no_pyc:
        _run(["-c", "import app.server"], _env(False, cache))     # populate __pycache__
    out = []
    for _ in range(runs):
        err = _run(["-c", PROBE], _env(no_pyc, cache))
        out.append(float(err.rsplit("@@", 1)[1].split()[0]))
    return out


def heaviest(top, no_pyc, cache):
    err = _run(["-X", "importtime", "-c", "import app.server"], _env(no_pyc, cache))
    rows = []
    for line in err.splitlines():
        if not line.startswith("import time:") or "|" not in line:
            continue
        _, self_us, cum_us, name = (p.strip() for p in line.replace("import time:", "|").split("|"))
        if self_us.isdigit():
            rows.append((int(cum_us), int(self_us), name))
    tops = {r[2].split(".")[0] for r in rows if not r[2].startswith(" ")}
    print(f"\nheaviest by cumulative time (of {len(rows)} modules, {len(tops)} top-level):")
    for cum, own, name in sorted(rows, reverse=True)[:top]:
        print(f"  {cum / 1000:8.1f} ms  (self {own / 1000:6.1f})  {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--no-pyc", action="store_true", help="compile from source, as on a read-only /var/task")
    ap.add_argument("--top", type=int, default=0, help="also list the N heaviest imports")
    a = ap.parse_args()

    cache = ROOT / ".import-profile-cache"     # never /tmp: shared machine
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir()
    try:
        t = timings(a.runs, a.no_pyc, cache)
        mode = "from source" if a.no_pyc else "cached .pyc"
        print(f"import app.server ({mode}, {a.runs} fresh processes): "
              f"best {min(t):.3f}s  median {statistics.median(t):.3f}s  worst {max(t):.3f}s")
        if a.top:
            heaviest(a.top, a.no_pyc, cache)
    finally:
        shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    main()
