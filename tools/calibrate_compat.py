"""
Regenerate THEME_REF in engine/compat.py: per-theme weight quartiles over
random pairs, so "strong" means the top quarter of ordinary pairs.

    python tools/calibrate_compat.py [pairs]
"""
import random
import statistics
import sys

from engine import compat

CITIES = [(41.88, -87.63), (40.71, -74.01), (34.05, -118.24),
          (51.51, -0.13), (19.43, -99.13), (-33.87, 151.21)]


def main(n=600):
    rng = random.Random(7)

    def person():
        lat, lon = rng.choice(CITIES)
        return {"birth_ut": [rng.randint(1985, 2006), rng.randint(1, 12),
                             rng.randint(1, 28), rng.random() * 24, lat, lon]}
    weights = {k: [] for k, _t, _r in compat.THEMES}
    for _ in range(n):
        seen = {t["key"]: t["weight"] for t in compat.deep(person(), person())["themes"]}
        for k in weights:
            weights[k].append(seen.get(k, 0.0))
    print("THEME_REF = {")
    for k, v in weights.items():
        q = statistics.quantiles(v, n=4)
        print(f'    "{k}": ({q[0]:.2f}, {q[2]:.2f}),')
    print("}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 600)
