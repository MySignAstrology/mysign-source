# Data attribution

## GeoNames

`app/data/cities.tsv.gz` is derived from the GeoNames **cities5000** export —
every settlement over 15,000 people, with coordinates and IANA timezone.

- Source: https://www.geonames.org
- Licence: **Creative Commons Attribution 4.0** (CC BY 4.0)
- Retrieved: August 2026

Derived by keeping name, ascii name, admin-1 region, country, latitude,
longitude, timezone and population; sorted by population. No modification to the
underlying facts.

CC BY 4.0 requires attribution wherever the data is used, which includes the
shipped app — the birthplace picker credits GeoNames.

## Swiss Ephemeris

Planetary positions come from `pyswisseph`, the Python binding to Swiss
Ephemeris by Astrodienst AG.

- Source: https://www.astro.com/swisseph/
- Licence: **AGPL-3.0**, or a commercial licence from Astrodienst

**Decided 2026-11-27 (Som): AGPL.** My Sign is published under the AGPL v3 (`LICENSE`,
`NOTICE.md`), and the complete source of what runs in production is public at
https://github.com/MySignAstrology/mysign-source, linked from Settings > Credits, the home page footer and /terms.
Every deploy must publish the deployed commit there (section 13: users who
interact with it over a network must be offered its source).

## Constellation plates

The faint engraved figures behind a sign (`app/static/plates/`) are taken from
Johannes Hevelius, *Firmamentum Sobiescianum, sive Uranographia* (Gdańsk, 1690),
the atlas volume of his *Prodromus Astronomiae*.

- Source: Wikimedia Commons, files titled `Johannes Hevelius - Prodromus
  Astronomia - Volume III "Firmamentum Sobiescianum, sive uranographia" -
  Tavola … - <constellation>.jpg` (plates BB Aries to NN Pisces)
- Licence: **public domain** (marked so on each Commons file page; the work is
  from 1690)
- What we did to them: `tools/make_plates.py` lifts the ink off the photographed
  page and saves it as a one-channel mask, so the app can tint it. The scans
  themselves are not in the repo.

Fonts are listed with their licences in `site/fonts/LICENSES.txt`.
