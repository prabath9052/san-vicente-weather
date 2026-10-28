#!/usr/bin/env python3
"""Daily forecast verification: 1-day-ahead forecast vs station measurements, per local day (America/Argentina/Buenos_Aires).
Forecast: Open-Meteo Previous Runs API, hourly *_previous_day1 (the run issued ~24 h before each hour) for the app's
models (ECMWF IFS, GFS, ICON, GEM, best match); daily values per model, then the MEDIAN across models (as the app blends).
Observed: station hourly series (data/station-series.json 'h'): tmax=max(tx), tmin=min(tn), rain=sum(r), wind mean/max of
hourly mean wind, RH mean, ET0=day max of cumulative e0. A day needs >= 22 station hours. Output: data/verification.json (last 90 days).
Env HIST_FILES: extra series JSON files (colon-separated) for backfill."""
import json, os, sys, statistics as stt, urllib.request, datetime as dt
LAT, LON = -31.8506, -65.4294
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "best_match"]
VARS = ["temperature_2m", "precipitation", "wind_speed_10m", "relative_humidity_2m", "et0_fao_evapotranspiration"]
W2M = 4.87 / __import__("math").log(67.8 * 10 - 5.42)  # A-05: FAO-56 eq.47, 10 m -> 2 m (station anemometer at 2 m) = 0.748
OUT = "data/verification.json"; TZ = dt.timezone(dt.timedelta(hours=-3)); KEEP = 90
def hourly_station(files):
    H = {}
    for f in files:
        try: h = json.load(open(f))["h"]
        except Exception: continue
        for i in range(h["n"]):
            t = dt.datetime.fromtimestamp((h["t0"] + i * h["step"]) / 1000, TZ)
            row = {k: (v[i] if i < len(v) else None) for k, v in h["v"].items()}
            if row.get("t") is not None: H[t.strftime("%Y-%m-%dT%H")] = row
    return H
def obs_day(H, d):
    rows = [H[k] for k in sorted(H) if k.startswith(d)]
    if len(rows) < 22: return None
    g = lambda k: [r[k] for r in rows if r.get(k) is not None]
    r1 = lambda x, n=1: round(x, n) if x is not None else None
    w = g("w")
    return {"tmax": r1(max(g("tx") or g("t"))), "tmin": r1(min(g("tn") or g("t"))), "rain": r1(sum(g("r")), 1),
            "wmean": r1(stt.mean(w)) if w else None, "wmax": r1(max(w)) if w else None,
            "rh": r1(stt.mean(g("rh")), 0) if g("rh") else None, "et0": r1(max(g("e0")), 2) if g("e0") else None, "hours": len(rows)}
def fc_days(d0, d1):
    q = (f"https://previous-runs-api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&timezone=America%2FArgentina%2FBuenos_Aires"
         f"&wind_speed_unit=kmh&start_date={d0}&end_date={(dt.date.fromisoformat(d1) + dt.timedelta(days=1)).isoformat()}&hourly=" + ",".join(v + "_previous_day1" for v in VARS) + "&models=" + ",".join(MODELS))
    h = json.load(urllib.request.urlopen(q, timeout=60))["hourly"]; T = h["time"]; out = {}
    for day in sorted({t[:10] for t in T}):
        idx = [i for i, t in enumerate(T) if t.startswith(day)]
        # A-06: Open-Meteo hourly rain/ET0 refer to the PRECEDING hour, so the local day's total is hours 01:00..24:00 (next day 00:00)
        nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat() + "T00:00"
        ids = [i for i, t in enumerate(T) if (t.startswith(day) and not t.endswith("T00:00")) or t == nxt]; per = {k: [] for k in ["tmax", "tmin", "rain", "wmean", "wmax", "rh", "et0"]}
        for m in MODELS:
            col = lambda v: [h[f"{v}_previous_day1_{m}"][i] for i in idx if h.get(f"{v}_previous_day1_{m}") and h[f"{v}_previous_day1_{m}"][i] is not None]
            t, w, rh = col("temperature_2m"), col("wind_speed_10m"), col("relative_humidity_2m")
            sm = lambda v: [h[f"{v}_previous_day1_{m}"][i] for i in ids if h.get(f"{v}_previous_day1_{m}") and h[f"{v}_previous_day1_{m}"][i] is not None]
            p, e = sm("precipitation"), sm("et0_fao_evapotranspiration")
            if len(t) < 22: continue
            per["tmax"].append(max(t)); per["tmin"].append(min(t)); per["rain"].append(sum(p))
            if w: w = [x * W2M for x in w]; per["wmean"].append(stt.mean(w)); per["wmax"].append(max(w))
            if rh: per["rh"].append(stt.mean(rh))
            if e: per["et0"].append(sum(e))
        if not per["tmax"]: continue
        md = lambda k, n=1: round(stt.median(per[k]), n) if per[k] else None
        out[day] = {"tmax": md("tmax"), "tmin": md("tmin"), "rain": md("rain"), "wmean": md("wmean"), "wmax": md("wmax"),
                    "rh": md("rh", 0), "et0": md("et0", 2), "models": len(per["tmax"]), "w2m": True}
    return out
def main():
    try: V = json.load(open(OUT))
    except Exception: V = {"days": []}
    mig = 0  # A-05 backfill: stored forecast winds were 10 m; convert once (median of scaled = scaled median)
    for x in V["days"]:
        f = x.get("f", {})
        if not f.get("w2m"):
            for k in ("wmean", "wmax"):
                if f.get(k) is not None: f[k] = round(f[k] * W2M, 1)
            f["w2m"] = True; mig += 1
    if mig: print("verify: converted forecast wind to 2 m for", mig, "stored days")
    have = {x["d"] for x in V["days"]}
    files = ["data/station-series.json"] + [f for f in os.environ.get("HIST_FILES", "").split(":") if f]
    H = hourly_station(files)
    yday = (dt.datetime.now(TZ).date() - dt.timedelta(days=1)).isoformat()
    cand = sorted({k[:10] for k in H if k[:10] <= yday} - have)
    obs = {d: o for d in cand if (o := obs_day(H, d))}
    if not obs:
        if mig: json.dump(V, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
        print("verify: nothing new"); return False
    fc = fc_days(min(obs), max(obs)); new = 0
    for d, o in obs.items():
        if d in fc: V["days"].append({"d": d, "f": fc[d], "o": o}); new += 1
    V["days"] = sorted(V["days"], key=lambda x: x["d"])[-KEEP:]
    V.update(updated=dt.datetime.now(TZ).isoformat(timespec="minutes"),
             method="Forecast wind converted 10 m -> 2 m (x0.748). 1-day-ahead: Open-Meteo Previous Runs (previous_day1), median of ECMWF IFS, GFS, ICON, GEM, best match vs Fondomonte station")
    json.dump(V, open(OUT, "w"), ensure_ascii=False, separators=(",", ":")); print("verify: added", new, "days:", ", ".join(sorted(obs))); return new > 0
if __name__ == "__main__": main()
