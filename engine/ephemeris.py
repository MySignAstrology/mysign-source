"""
Where the sky comes from — one place, so every module computes the same one.

Swiss Ephemeris has two modes. Without data files it falls back to the Moshier
analytical theory, which is good to about an arcsecond for planets and cannot
compute Chiron at all — Chiron was silently missing from every chart because of
it. With the Astrodienst data files (engine/ephe/, compressed from NASA JPL's
DE431, covering 1800–2399) it is the full-precision ephemeris professional
astrologers use, and Chiron is back.

tests/test_horizons.py holds that claim to account against NASA JPL Horizons
directly. The files are 2 MB and ship inside the Lambda package.
"""
import os
import pathlib

import swisseph as swe

EPHE_DIR = pathlib.Path(__file__).resolve().parent / "ephe"
FILES = ("sepl_18.se1", "semo_18.se1", "seas_18.se1")

HAVE_FILES = all((EPHE_DIR / f).is_file() for f in FILES)

if HAVE_FILES:
    # Both, on purpose. On Linux, Swiss Ephemeris keeps its path per thread, so
    # set_ephe_path here only reaches the importing thread — every request the
    # server answers runs on a worker thread, which would silently fall back to
    # Moshier for planets and fail outright on Chiron. The library reads
    # SE_EPHE_PATH whenever a thread has no path of its own, so the environment
    # variable is what actually covers the threads. macOS builds share one path
    # process-wide, which is why this never showed locally.
    os.environ["SE_EPHE_PATH"] = str(EPHE_DIR)
    swe.set_ephe_path(str(EPHE_DIR))
    BASE = swe.FLG_SWIEPH
else:
    swe.set_ephe_path(None)
    BASE = swe.FLG_MOSEPH

FLAGS = BASE | swe.FLG_SPEED

# What we can state, and stand behind, about where positions come from. Shown to
# the model and on the accuracy page — both read it from here so they cannot
# drift from what the engine actually did.
SOURCE = {
    "engine": f"Swiss Ephemeris {swe.version}",
    "data": ("Astrodienst data files derived from NASA JPL DE431" if HAVE_FILES
             else "Moshier analytical fallback (data files missing)"),
    "full_precision": HAVE_FILES,
    "zodiac": "tropical",
    "frame": "apparent geocentric, true equinox of date",
}


def time_zone_rules():
    from engine.localtime import TZ_RULES
    return TZ_RULES
