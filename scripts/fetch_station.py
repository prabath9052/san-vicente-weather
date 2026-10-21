#!/usr/bin/env python3
"""Fetch current conditions from the LI-COR Cloud (HOBOlink) RX3000 'Fondomonte Weather'
and write data/station.json (readings only, never the token).
Sensor choice: air temp = 20530638-1 (same probe as RH/dew point; 20496238-1 is a second
temperature probe that reads ~3 °C higher, likely sun-exposed/unshielded — kept as temp_alt).
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
end = int(time.time() * 1000); start = min(int(midnight.timestamp() * 1000), end - 24 * 3600000)
series = {}
path = f"/data?deviceSerialNumber={DEV}&startTime={start}&endTime={end}"
for _ in range(20):
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
  "tempAlt": r1(last("20496238-1")[0]),
  "rh": r1(last("20530638-2")[0], 0), "dewPoint": r1(last("20530638-3")[0]),
  "wind": r1((last("21796781-1")[0] or 0) * KMH) if last("21796781-1")[0] is not None else r1((last("20530319-1")[0] or 0) * KMH),
  "gust": r1(last("21796781-2")[0] * KMH) if last("21796781-2")[0] is not None else None,
  "gustMaxToday": r1(max(today("21796781-2")) * KMH) if today("21796781-2") else None,
  "windDir": r1(last("21796781-3")[0], 0),
  "solar": r1(last("20530536-1")[0], 0),
  "wetness": r1(last("20851391-1")[0], 0),
  "etToday": r1(sum(et), 2) if et else r1(next((x.get("latest") for x in dev["sensors"] if x["sensorSerialNumber"] == "20537254-1"), None), 2),
  "rainToday": r1(sum(r[1] for r in rain if r[0] >= mid_ms)) if rain else None,
  "rain24h": r1(sum(r[1] for r in rain if r[0] >= d24)) if rain else None,
  "soilWater": r1(soil, 3) if soil is not None and 0 <= soil <= 0.6 else None,
}
os.makedirs("data", exist_ok=True)
old = None
try: old = json.load(open("data/station.json"))
except Exception: pass
if old and {k: v for k, v in old.items() if k != "fetched"} == {k: v for k, v in out.items() if k != "fetched"}:
    print("unchanged"); sys.exit(0)
json.dump(out, open("data/station.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
