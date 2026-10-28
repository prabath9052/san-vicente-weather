#!/usr/bin/env python3
"""Fetch current conditions from the LI-COR Cloud (HOBOlink) RX3000 'Fondomonte Weather'
and write data/station.json (readings only, never the token).
Sensor choice: air temp = 20530638-1 (same probe as RH/dew point). 20496238-1 is a SOIL
temperature probe -> soilTemp (current) + soilTempMax/soilTempMin (today's records).
Wind/gust/direction = 21796781 (combo anemometer with direction); 20530319 kept as alt.
Soil water 20527026-1 only shown if 0..0.6 m3/m3."""
import json, os, sys, time, datetime as dt, urllib.request
TOKEN = os.environ["HOBOLINK_API_TOKEN"]
DEV = "20537254"; API = "https://api.licor.cloud/v2"
TZ = dt.timezone(dt.timedelta(hours=-3))
def get(path):
    r = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + TOKEN})
    return json.load(urllib.request.urlopen(r, timeout=60))
now = dt.datetime.now(TZ)
midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
end = int(time.time() * 1000); start = min(int(midnight.timestamp() * 1000), end - 8 * 24 * 3600000)
series = {}
path = f"/data?deviceSerialNumber={DEV}&startTime={start}&endTime={end}"
for _ in range(200):
    d = get(path)
    for s in d.get("sensors", []):
        for blk in s.get("data", []):
            if blk.get("dataType", "CURRENT") != "CURRENT": continue
            series.setdefault(s["sensorSerialNumber"], []).extend(r for r in blk["records"] if r[1] is not None)
    if not d.get("moreResults"): break
    last = max((r[0] for v in series.values() for r in v), default=start)
    path = f"/data?deviceSerialNumber={DEV}&startTime={last+1}&endTime={end}"
for v in series.values(): v.sort()
dev = get("/devices")["devices"]; dev = next(x for x in dev if x["deviceSerialNumber"] == DEV)
mid_ms = midnight.timestamp() * 1000; d24 = end - 24 * 3600000
def last(sn):
    v = series.get(sn); return (v[-1][1], v[-1][0]) if v else (None, None)
def today(sn): return [r[1] for r in series.get(sn, []) if r[0] >= mid_ms]
def r1(x, n=1): return None if x is None else round(x, n)
KMH = 3.6
t, ts = last("20530638-1"); tt = today("20530638-1")
rain = series.get("10704900-1")
soil = last("20527026-1")[0]
et = today("20537254-1")
ts_all = max((v[-1][0] for v in series.values() if v), default=None)
out = {
  "station": dev["deviceName"], "serial": DEV,
  "lastConnection": dev.get("lastConnectionTime"),
  "observed": dt.datetime.fromtimestamp(ts_all / 1000, TZ).isoformat() if ts_all else None,
  "fetched": now.isoformat(timespec="seconds"),
  "temp": r1(t), "tempMax": r1(max(tt)) if tt else None, "tempMin": r1(min(tt)) if tt else None,
  "soilTemp": r1(last("20496238-1")[0]),
  "soilTempMax": r1(max(today("20496238-1"))) if today("20496238-1") else None,
  "soilTempMin": r1(min(today("20496238-1"))) if today("20496238-1") else None,
  "rh": r1(last("20530638-2")[0], 0), "dewPoint": r1(last("20530638-3")[0]),
  "wind": r1((last("21796781-1")[0] or 0) * KMH) if last("21796781-1")[0] is not None else r1((last("20530319-1")[0] or 0) * KMH),
  "gust": r1(last("21796781-2")[0] * KMH) if last("21796781-2")[0] is not None else None,
  "gustMaxToday": r1(max(today("21796781-2")) * KMH) if today("21796781-2") else None,
  "windDir": r1(last("21796781-3")[0], 0),
  "solar": r1(last("20530536-1")[0], 0),
  "wetness": r1(last("20851391-1")[0], 0),
  # device ET sensor logs one daily total stamped at the START of that local day
  "etYesterday": r1(next((r[1] for r in series.get("20537254-1", []) if r[0] == int(mid_ms) - 24 * 3600000), None), 2),
  "rainToday": r1(sum(r[1] for r in rain if r[0] > mid_ms)) if rain else None,
  "rain24h": r1(sum(r[1] for r in rain if r[0] >= d24)) if rain else None,
  "soilWater": r1(soil, 3) if soil is not None and 0 <= soil <= 0.6 else None,
}
os.makedirs("data", exist_ok=True)
# ---- compact series for the "today's graphs" popup -> data/station-series.json
SER = {"t": ("20530638-1", "avg", 1), "st": ("20496238-1", "avg", 1), "rh": ("20530638-2", "avg", 0),
       "dp": ("20530638-3", "avg", 1), "w": ("21796781-1", "avg", 1), "g": ("21796781-2", "max", 1),
       "wd": ("21796781-3", "vec", 0), "r": ("10704900-1", "sum", 1), "lw": ("20851391-1", "avg", 0),
       "sr": ("20530536-1", "avg", 0), "tn": ("20530638-1", "min", 1), "tx": ("20530638-1", "max", 1)}
# ---- derived series (documented in README-style comments):
# ss  = sunshine duration, minutes per bucket (WMO: time with global radiation >= 120 W/m2),
#       counted from the raw logger records: each raw record >= 120 W/m2 adds its logging
#       interval (gap to previous record, capped at 15 min).
# e0  = reference evapotranspiration ET0 (FAO-56 Penman-Monteith, hourly/short-step form,
#       eq. 53), accumulated since local midnight (mm). Uses bucket air temp, RH, wind
#       (anemometer treated as 2 m), solar radiation; Rn from Rs (albedo 0.23) and FAO-56
#       net longwave with Rs/Rso; G = 0.1 Rn by day, 0.5 Rn by night; elevation 399 m.
LAT, ELEV = -31.8506, 399.0
def ghi_cs(ts_ms):
    """clear-sky global irradiance (W/m2) at the middle of the 5-min record ending ts_ms; 0 if sun < 3 deg"""
    loc = dt.datetime.fromtimestamp((ts_ms - 150000) / 1000, TZ); J = loc.timetuple().tm_yday
    de = 0.409 * math.sin(2 * math.pi * J / 365 - 1.39); b = 2 * math.pi * (J - 81) / 364
    Sc = 0.1645 * math.sin(2 * b) - 0.1255 * math.cos(b) - 0.025 * math.sin(b)
    w = math.pi / 12 * (loc.hour + loc.minute / 60 + loc.second / 3600 + 0.06667 * (45 - 65.4294) + Sc - 12)
    phi = math.radians(LAT); cz = math.sin(phi) * math.sin(de) + math.cos(phi) * math.cos(de) * math.cos(w)
    if cz < math.sin(math.radians(3)): return 0.0
    return (0.75 + 2e-5 * ELEV) * 1367 * (1 + 0.033 * math.cos(2 * math.pi * J / 365)) * cz
# Sunshine (ratio method): a pyranometer can't see direct beam, so count minutes when global
# radiation >= 40 % of clear-sky global radiation with the sun > 3 deg (approximates WMO 120 W/m2 direct).
SUN_K = 0.4
def sun_raw(t0, step, n):
    o = [0.0] * n; prev = None
    for ts_, x in series.get("20530536-1", []):
        dtm = min(15, (ts_ - prev) / 60000) if prev else 5; prev = ts_
        i = int((ts_ - 1 - t0) // step)
        cs = ghi_cs(ts_) if 0 <= i < n else 0
        if cs > 0 and x >= SUN_K * cs: o[i] += dtm
    return o
def et0_series(t0, step, n, v, last_frac=1.0):
    P = 101.3 * ((293 - 0.0065 * ELEV) / 293) ** 5.26; gam = 0.000665 * P
    hrs = step / 3600000; out = []; cum = 0; ratio = 0.7; day = None
    for i in range(n):
        x = t0 + i * step; loc = dt.datetime.fromtimestamp(x / 1000, TZ)
        if loc.date() != day: day = loc.date(); cum = 0
        T, RH, U, SR = v["t"][i], v["rh"][i], v["w"][i], v["sr"][i]
        if None in (T, RH, SR): out.append(round(cum, 2) if cum else (0 if i else None)); continue
        U = (U or 0) / KMH
        # extraterrestrial radiation for this interval (FAO-56 eq. 28)
        J = loc.timetuple().tm_yday; phi = math.radians(LAT)
        dr = 1 + 0.033 * math.cos(2 * math.pi * J / 365); de = 0.409 * math.sin(2 * math.pi * J / 365 - 1.39)
        b = 2 * math.pi * (J - 81) / 364; Sc = 0.1645 * math.sin(2 * b) - 0.1255 * math.cos(b) - 0.025 * math.sin(b)
        tm = loc.hour + loc.minute / 60 + hrs / 2; Lz = 45; Lm = 65.4294  # degrees west of Greenwich
        w = math.pi / 12 * ((tm + 0.06667 * (Lz - Lm) + Sc) - 12)
        ws = math.acos(-math.tan(phi) * math.tan(de)); w1 = max(-ws, w - math.pi * hrs / 24); w2 = min(ws, w + math.pi * hrs / 24)
        Ra = 0 if w1 >= w2 else 12 * 60 / math.pi * 0.082 * dr * ((w2 - w1) * math.sin(phi) * math.sin(de) + math.cos(phi) * math.cos(de) * (math.sin(w2) - math.sin(w1)))
        Rs = SR * step / 1000 / 1e6; Rso = (0.75 + 2e-5 * ELEV) * Ra
        if Rso > 0.01 * hrs: ratio = min(1.0, Rs / Rso)
        es = 0.6108 * math.exp(17.27 * T / (T + 237.3)); ea = es * RH / 100
        Rnl = 4.903e-9 / 24 * hrs * (T + 273.16) ** 4 * (0.34 - 0.14 * math.sqrt(max(ea, 0))) * (1.35 * ratio - 0.35)
        Rn = 0.77 * Rs - Rnl; G = (0.04 if Ra > 0 else 0.2) * Rn; cd = 0.24 if Ra > 0 else 0.96  # ASCE-EWRI standardized short reference
        D = 4098 * es / (T + 237.3) ** 2
        e = (0.408 * D * (Rn - G) + gam * (37 * hrs) / (T + 273) * U * (es - ea)) / (D + gam * (1 + cd * U))
        if i == n - 1: e *= last_frac  # A-04: unfinished last interval counts only its elapsed part
        cum += max(0, e); out.append(round(cum, 2))
    return out
import math
def bucket(t0, step, n):
    v = {}
    for k, (sn, how, nd) in SER.items():
        b = [[] for _ in range(n)]
        for ts_, x in series.get(sn, []):
            i = int((ts_ - 1 - t0) // step)  # record stamped at interval end
            if 0 <= i < n: b[i].append(x)
        sc = KMH if k in ("w", "g") else 1
        o = []
        for a in b:
            if not a: o.append(None); continue
            if how == "avg": y = sum(a) / len(a) * sc
            elif how == "max": y = max(a) * sc
            elif how == "min": y = min(a) * sc
            elif how == "sum": y = sum(a); nd = 2
            else: y = math.degrees(math.atan2(sum(math.sin(math.radians(z)) for z in a), sum(math.cos(math.radians(z)) for z in a))) % 360
            o.append(round(y, nd) if nd else int(round(y)))
        v[k] = o
    v["ss"] = [round(x) for x in sun_raw(t0, step, n)]
    lf = min(1.0, max(0.0, (ts_all - (t0 + (n - 1) * step)) / step)) if ts_all else 1.0
    v["e0"] = et0_series(t0, step, n, v, lf)
    return {"t0": t0, "step": step, "n": n, "v": v}
Q = 900000; H = 3600000
q0 = int(mid_ms) - 2 * 24 * H  # 15-min buckets from local midnight two days ago (Today / 24 h / 48 h)
qn = int((end - q0) // Q) + 1
h0 = int(mid_ms) - 7 * 24 * H  # hourly buckets from local midnight 7 days ago
hn = int((end - h0) // H) + 1
ser = {"fetched": now.isoformat(timespec="seconds"), "tz": "-03:00", "midnight": int(mid_ms), "etDev": out["etYesterday"],
       "q": bucket(q0, Q, qn), "h": bucket(h0, H, hn)}
_e = [x for x in ser["q"]["v"]["e0"][int((mid_ms - q0) // Q):] if x is not None]
out["et0Today"] = _e[-1] if _e else None
json.dump(ser, open("data/station-series.json", "w"), ensure_ascii=False, separators=(",", ":"))
print("series:", qn, "x15min,", hn, "x1h")
old = None
try: old = json.load(open("data/station.json"))
except Exception: pass
if old and {k: v for k, v in old.items() if k != "fetched"} == {k: v for k, v in out.items() if k != "fetched"}:
    print("unchanged"); sys.exit(0)
json.dump(out, open("data/station.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
