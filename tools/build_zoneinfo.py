"""
Compile the time zone rules we ship — IANA tzdb *with* backzone — into
engine/zoneinfo/.

    python tools/build_zoneinfo.py                 # downloads tzdata<VERSION>.tar.gz
    python tools/build_zoneinfo.py --src DIR       # an already-unpacked tzdata tree
    python tools/build_zoneinfo.py --version 2026g

Why not the `tzdata` package: since tzdb 2022b the IANA project folds zones that
agree after 1970 into links, and moves their older history to a file called
`backzone` that the default build leaves out. The PyPI `tzdata` wheel is that
default build, so Atlantic/Reykjavik is a link to Africa/Abidjan (Iceland kept
standard time at UTC-1 until 1968), Europe/Oslo and Stockholm to Berlin,
Amsterdam to Brussels, Curaçao to Puerto Rico and a few dozen African zones to
Lagos, Nairobi or Abidjan. For someone born there before 1970 the conversion to
UT was off by 15 minutes to two hours — up to a whole rising sign. Backzone is
the project's own best record of those histories, so we compile it in.

The merge is what tzdb's ziguard.awk does for `make PACKRATDATA=backzone
PACKRATLIST=`: wherever backzone defines a Zone, the Link of the same name in
the main data or `backward` is dropped, and the backzone Zone wins. It is done
here in plain Python rather than by running the tarball's Makefile, so nothing
from the download is ever executed — only the system `zic` reads it.

The output is regular TZif files in zic's "fat" format (see build()), one per name, links included as copies so a zip or a checkout
that flattens hard links cannot lose one. engine/localtime.py points zoneinfo
at this directory for the whole process.
"""
import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "engine" / "zoneinfo"
VERSION = "2026f"
URL = "https://data.iana.org/time-zones/releases/tzdata{v}.tar.gz"

# tzdb's TDATA minus `factory` (the "Factory" placeholder zone is no birthplace).
MAIN = ["africa", "antarctica", "asia", "australasia", "europe",
        "northamerica", "southamerica", "etcetera", "backward"]

_ZONE = re.compile(r"^Zone\s+(\S+)", re.M)
_LINK = re.compile(r"^Link\s+(\S+)\s+(\S+)")


def merged(src):
    """The main data plus backzone, with every name defined exactly once."""
    back = (src / "backzone").read_text(encoding="utf-8")
    zones_in_back = set(_ZONE.findall(back))
    main_text = {f: (src / f).read_text(encoding="utf-8") for f in MAIN}
    zones_in_main = set()
    for t in main_text.values():
        zones_in_main |= set(_ZONE.findall(t))

    # Backzone's own links (Iceland -> Atlantic/Reykjavik) re-point names the
    # main data links elsewhere (Iceland -> Africa/Abidjan); backzone's wins.
    back_lines, links_in_back = [], set()
    for line in back.splitlines():
        m = _LINK.match(line)
        if m and (m.group(2) in zones_in_main
                  or m.group(1) not in zones_in_main | zones_in_back):
            continue                          # a real Zone has the name, or no target
        if m:
            links_in_back.add(m.group(2))
        back_lines.append(line)

    out = []
    for f in MAIN:
        for line in main_text[f].splitlines():
            m = _LINK.match(line)
            if m and m.group(2) in zones_in_back | links_in_back:
                continue                      # backzone defines this name instead
            out.append(line)
    out += back_lines
    return "\n".join(out) + "\n"


def fetch(version, into):
    url = URL.format(v=version)
    tgz = into / f"tzdata{version}.tar.gz"
    print(f"→ {url}")
    with urllib.request.urlopen(url, timeout=60) as r, open(tgz, "wb") as fh:
        shutil.copyfileobj(r, fh)
    print(f"   sha256 {hashlib.sha256(tgz.read_bytes()).hexdigest()}")
    src = into / "src"
    src.mkdir()
    with tarfile.open(tgz) as t:
        t.extractall(src, filter="data")
    return src


def build(src, out=OUT):
    version = (src / "version").read_text().strip()
    with tempfile.TemporaryDirectory() as tmp:
        zi = pathlib.Path(tmp) / "main.zi"
        zi.write_text(merged(src), encoding="utf-8")
        stage = pathlib.Path(tmp) / "zoneinfo"
        # -b fat: every transition written out explicitly. The "slim" default
        # leans on the reader extending the POSIX footer, and macOS's zic
        # (2022g) wrote a slim America/Ojinaga that Python read an hour wrong
        # for November 2022. Fat costs a few hundred KB and removes the doubt.
        subprocess.run(["zic", "-b", "fat", "-d", str(stage), str(zi)], check=True)
        if out.exists():
            shutil.rmtree(out)
        # copytree follows nothing special: zic's hard links become plain copies.
        shutil.copytree(stage, out)
    (out / "VERSION").write_text(f"{version}+backzone\n")
    names = [p for p in out.rglob("*") if p.is_file() and p.name != "VERSION"]
    size = sum(p.stat().st_size for p in names)
    print(f"   {len(names)} zones, {size / 1024:.0f} KiB, tzdb {version} + backzone -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=pathlib.Path, help="unpacked tzdata directory")
    ap.add_argument("--version", default=VERSION)
    a = ap.parse_args()
    if not shutil.which("zic"):
        sys.exit("zic not found (it ships with macOS and the tzcode package on Linux)")
    if a.src:
        build(a.src)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            build(fetch(a.version, pathlib.Path(tmp)))
