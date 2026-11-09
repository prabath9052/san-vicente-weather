#!/usr/bin/env python3
"""Offline test of forecast_skill.py (no network). Usage: python3 scripts/test_forecast_skill.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forecast_skill as fs
M = fs.MODELS
def day(i, obs, fc):  # fc: {model: (tmax, tmin, rain)}
    return {"d": f"2026-10-{i:02d}", "o": {"tmax": obs[0], "tmin": obs[1], "rain": obs[2]},
            "fm": {m: {"tmax": v[0], "tmin": v[1], "rain": v[2]} for m, v in fc.items()}}
# GFS: 0.5 too cool on tmax; the others 2.5-3 too cool -> GFS gets the largest tmax weight; whole blend cool -> positive correction
D = [day(i, (30, 10, 0), {"gfs_seamless": (29.5, 11, 0), "ecmwf_ifs025": (27, 10.5, 2), "icon_seamless": (27.5, 12, 1),
                          "gem_seamless": (27, 11, 5), "best_match": (27.5, 10.5, 0)}) for i in range(1, 16)]
sk = fs.compute(D)
w = sk["weights"]["tmax"]
assert abs(sum(w.values()) - 1) < 0.01, w
assert max(w, key=w.get) == "gfs_seamless", w
assert min(w.values()) >= fs.FLOOR / len(M) - 1e-3, w                # floor holds
assert sk["bias"]["tmax"] < 0 and sk["corr"]["tmax"] > 0, sk          # cool bias -> warm correction
nb = fs.forecast(D[0]["fm"], sk, "tmax"); assert abs(nb - 30) < 0.01, nb  # corrected blend hits the station on this synthetic history
assert sk["corr"]["tmin"] < 0                                          # warm tmin bias -> cooling correction
r = fs.forecast(D[0]["fm"], sk, "rain"); assert r >= 0 and r < 1, r   # weighted median ignores the wet outlier
# cap
Dc = [day(i, (40, 10, 0), {m: (30, 10, 0) for m in M}) for i in range(1, 16)]
assert fs.compute(Dc)["corr"]["tmax"] == fs.CAP
# too little history -> equal weights, no correction
sk2 = fs.compute(D[:fs.MIN_DAYS - 1])
assert set(sk2["weights"]["tmax"].values()) == {round(1 / len(M), 3)} and sk2["corr"]["tmax"] == 0.0, sk2
# out-of-sample: 'before' excludes the test day and later days
assert fs.compute(D, before="2026-10-12")["n"] == 11
# weighted median with equal weights = plain median
assert fs.blend({m: {"rain": v} for m, v in zip(M, [0, 1, 2, 3, 10])}, {m: 1 for m in M}, "rain") == 2
assert fs.blend({m: {"rain": v} for m, v in zip(M[:4], [0, 1, 2, 3])}, {m: 1 for m in M}, "rain") == 1.5
# day_blend: only days before the previous day are used (no peeking at the test day or the day it was issued)
b = fs.day_blend(D, D[14], "live"); assert b["n"] == 13 and b["src"] == "live" and abs(b["tmax"] - 30) < 0.05, b
assert fs.day_blend(D, {"d": "2026-10-20"}, "live") is None
print("forecast_skill: all checks passed")
