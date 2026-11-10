#!/usr/bin/env python3
"""Offline tests for station_health.py (no network, no secrets). Usage: python3 scripts/test_station_health.py"""
import os, sys, json, tempfile, subprocess, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import station_health as H
TZ = H.TZ; NOW = dt.datetime(2026, 11, 10, 14, 0, tzinfo=TZ)
def series(now=NOW, n=40, **cols):
    """15-min buckets ending at `now`; default plausible varying values; override columns with lists or a constant"""
    step = 900000; t0 = int(now.timestamp() * 1000) - (n - 1) * step
    v = {"t": [20 + (i % 7) * 0.3 for i in range(n)], "rh": [50 + (i % 5) for i in range(n)], "w": [5 + (i % 4) for i in range(n)],
         "g": [9 + (i % 4) for i in range(n)], "r": [0] * n, "sr": [600 + i for i in range(n)], "st": [22] * n}
    for k, x in cols.items(): v[k] = x if isinstance(x, list) else [x] * n
    return {"q": {"t0": t0, "step": step, "n": n, "v": v}}
def station(now=NOW, age_min=10, **kw):
    s = {"observed": (now - dt.timedelta(minutes=age_min)).isoformat(), "temp": 21, "rh": 52, "dewPoint": 10, "wind": 6, "gust": 10, "solar": 640, "soilTemp": 22}
    s.update(kw); return s
ok = lambda c, m: (_ for _ in ()).throw(AssertionError(m)) if not c else None
# 1 healthy station: nothing
ok(H.evaluate(station(), series(), NOW) == {}, H.evaluate(station(), series(), NOW))
# 2 offline after > 120 min; no sensor checks on stale data
p = H.evaluate(station(age_min=150, rh=0), series(), NOW); ok(list(p) == ["offline"], p)
ok(H.evaluate(station(age_min=110), series(), NOW) == {}, "110 min is still fine")
# 3 state machine: offline pushes once, no repeats, recovery push, no re-push inside the cool-down
S = {}
S, out = H.step(S, p, NOW); ok([(a, b) for a, b, _ in out] == [("start", "offline")], out); ok(S["ok"] is False and "offline" in S["active"], S)
S, out = H.step(S, p, NOW + dt.timedelta(minutes=15)); ok(out == [], "no duplicate push")
S, out = H.step(S, {}, NOW + dt.timedelta(minutes=60)); ok([(a, b) for a, b, _ in out] == [("end", "offline")], out); ok(S["ok"] and not S["active"], S)
S, out = H.step(S, p, NOW + dt.timedelta(minutes=90)); ok(out == [], "flap inside 6 h cool-down: banner only, no push"); ok("offline" in S["active"], S)
S, out = H.step(S, {}, NOW + dt.timedelta(minutes=120)); ok(out == [], "no 'back online' for an un-notified episode")
S, out = H.step(S, p, NOW + dt.timedelta(hours=8)); ok([(a, b) for a, b, _ in out] == [("start", "offline")], "pushes again after the cool-down")
# 4 sensor problem needs confirmation (2 checks >= 20 min apart); a one-off glitch is forgotten
rh0 = H.evaluate(station(rh=0), series(), NOW); ok("rh" in rh0 and "impossible" in rh0["rh"]["en"] and "imposible" in rh0["rh"]["es"], rh0)
S = {}; S, out = H.step(S, rh0, NOW); ok(out == [] and S["ok"], "not yet confirmed")
S, out = H.step(S, rh0, NOW + dt.timedelta(minutes=10)); ok(out == [], "10 min is too soon")
S, out = H.step(S, rh0, NOW + dt.timedelta(minutes=25)); ok([(a, b) for a, b, _ in out] == [("start", "rh")], out)
S, out = H.step(S, {}, NOW + dt.timedelta(minutes=40)); ok([(a, b) for a, b, _ in out] == [("end", "rh")], out)
G = {}; G, out = H.step(G, rh0, NOW); G, out = H.step(G, {}, NOW + dt.timedelta(minutes=15)); ok(out == [] and "rh" not in G["issues"], G)
# 5 other impossible readings
ok("rh" in H.evaluate(station(rh=104), series(), NOW), "RH > 100")
ok("temp" in H.evaluate(station(temp=61), series(), NOW), "temp > 50")
ok("temp" in H.evaluate(station(temp=None), series(), NOW), "missing temp")
ok("rain" in H.evaluate(station(), series(r=[0] * 39 + [-0.4]), NOW), "negative rain")
ok("wind" in H.evaluate(station(wind=-3), series(), NOW), "negative wind")
ok("dew" in H.evaluate(station(dewPoint=25), series(), NOW), "dew point above air temp")
night = dt.datetime(2026, 11, 10, 2, 0, tzinfo=TZ)
ok("solar" in H.evaluate(station(night, solar=0), series(night, sr=[0] * 39 + [250]), night), "solar at night")
ok(H.evaluate(station(night, solar=0), series(night, sr=0), night) == {}, "dark night is fine")
ok("temp" in H.evaluate(station(), series(t=18.4), NOW), "air temp stuck for 6 h")
ok("rh" not in H.evaluate(station(rh=100), series(rh=100), NOW), "fog at 100 % for hours is plausible")
ok("wind" not in H.evaluate(station(wind=0), series(w=0, g=0), NOW), "calm for hours is plausible")
# 6 push texts EN/ES, never mention spraying
for kind, k in (("start", "offline"), ("end", "offline"), ("start", "rh"), ("end", "rh")):
    x = {"en": "x", "es": "x"}
    for lang in ("en", "es"):
        t, b = H.message(kind, k, x, lang); ok(t and b and "spray" not in (t + b).lower() and "pulveri" not in (t + b).lower(), (t, b))
# 7 end-to-end dry run of main(): stale station -> 'would push', state written, no secrets needed
d = tempfile.mkdtemp(); os.makedirs(d + "/data")
json.dump(station(age_min=200), open(d + "/data/station.json", "w")); json.dump(series(), open(d + "/data/station-series.json", "w"))
env = {k: v for k, v in os.environ.items() if k not in ("VAPID_PRIVATE_KEY", "FB_BOT_PASSWORD")}; env.update(DRY_RUN="1", SIM_NOW=NOW.isoformat())
r = subprocess.run([sys.executable, os.path.abspath(H.__file__)], cwd=d, env=env, capture_output=True, text=True)
ok(r.returncode == 0 and "would push start offline" in r.stdout, r.stdout + r.stderr)
st = json.load(open(d + "/data/health-state.json")); ok(st["active"]["offline"]["since"], st)
r = subprocess.run([sys.executable, os.path.abspath(H.__file__)], cwd=d, env=env, capture_output=True, text=True); ok("pushes: 0" in r.stdout, r.stdout)
print("station_health: all checks passed")
