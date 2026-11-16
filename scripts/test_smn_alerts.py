#!/usr/bin/env python3
"""Offline tests for smn_alerts.py with saved CAP samples (scripts/testdata/smn). No network, no secrets, never sends."""
import os, sys, json, tempfile, subprocess, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import smn_alerts as S
D = os.path.join(HERE, "testdata", "smn"); TZ = S.TZ
def ok(c, m):
    if not c: raise AssertionError(m)
def cap(name, url=None):
    return S.parse_cap(open(os.path.join(D, name), "rb").read(), url or "https://ssl.smn.gob.ar/feeds/CAP/" + ("avisocortoplazo/" if name.startswith("short") else "xml_generados/") + name)
NOW = dt.datetime(2026, 11, 16, 16, 30, tzinfo=TZ)
# feed
F = S.parse_feed(open(os.path.join(D, "feed.xml"), "rb").read()); ok(len(F) == 5 and F[0]["link"].endswith("2026_11_16_2015_cap_es.xml"), F)
# department lists (accents, case, abbreviations with dots)
d = S.depts("LA RIOJA: GRAL BELGRANO - GRAL JUAN F.  QUIROGA - ROSARIO V.  PENALOZA. CORDOBA: MINAS. "); ok(d.get("CORDOBA") == ["MINAS"] and "LA RIOJA" in d, d)
ok("SAN ALBERTO" in S.depts("Córdoba: Pocho - san alberto - San Javier.")["CORDOBA"], "accent/case-insensitive")
# 1 matching San Alberto short-term warning (SIMULATED sample) -> by name; UTC quirk corrected when first seen
a = cap("short_SIMULATED_sanalberto_2026_11_16_2015_cap_es.xml"); ok(a["kind"] == "short" and a["level"] == "orange", a)
ok(S.match(a) == "name", S.match(a)); a = S.fix_utc(a, NOW); ok(a.get("utcFix") and a["onset"].startswith("2026-11-16T17:15") and a["expires"].startswith("2026-11-16T18:15"), a)
ok(S.active(a, NOW), "active at 16:30"); ok(not S.active(a, dt.datetime(2026, 11, 16, 18, 20, tzinfo=TZ)), "expired at 18:20")
# 2 non-matching: the real Minas short-term warning (its polygon does not contain the station either)
m = cap("short_minas_2026_11_16_1903_cap_es.xml"); ok(S.match(m) is None, "Minas must not match")
# name list wins over the polygon: Minas listed but polygon covering the station -> no match
m2 = dict(m, polys=[[(-31, -66), (-31, -65), (-32.5, -65), (-32.5, -66), (-31, -66)]]); ok(S.match(m2) is None, "department list decides")
# real yellow/orange alerts (polygons only): Cordillera zones contain the station, Llanura 71 does not
o = cap("alert_orange_storms_cordillera_8.xml"); y = cap("alert_yellow_storms_cordillera_20.xml"); l = cap("alert_yellow_storms_llanura_71.xml")
ok((o["level"], S.match(o)) == ("orange", "polygon"), o["level"]); ok((y["level"], S.match(y)) == ("yellow", "polygon"), y["level"]); ok(S.match(l) is None, "Llanura 71")
ok(S.fix_utc(dict(o), NOW)["onset"] == o["onset"], "regular alerts are not shifted")
# 3 expired one is ignored
ok(not S.active(o, dt.datetime(2026, 11, 17, 3, 0, tzinfo=TZ)) and S.active(y, dt.datetime(2026, 11, 17, 3, 0, tzinfo=TZ)), "expiry")
ok(not S.active(dict(o, msgType="Cancel"), NOW), "cancelled"); ok(not S.active(dict(o, status="Test"), NOW), "test messages")
# 4 dedupe / state machine
st, p = S.step({}, [o, y], NOW); ok([(k, x["level"]) for k, x in p] == [("start", "orange")], p)   # yellow block right after the orange one: covered
st, p = S.step(st, [o, y], NOW); ok(p == [], "no duplicate on the next check")
o2 = dict(o, id=o["id"] + ".upd"); st, p = S.step(st, [o2, y], NOW); ok(p == [], "re-issued update of the same alert: no push")
r = dict(o, id="red1", level="red"); st, p = S.step(st, [o2, y, r], NOW); ok([(k, x["level"]) for k, x in p] == [("upgrade", "red")], p)
st, p = S.step(st, [a], NOW); ok([(k, x["kind"]) for k, x in p] == [("start", "short")], "a short-term warning is pushed separately")
st, p = S.step(st, [], NOW); ok([k for k, _ in p] == ["end"], p); st, p = S.step(st, [], NOW); ok(p == [], "'ended' once")
st2, p = S.step({}, [], NOW); ok(p == [], "nothing, nothing")
# push wording EN/ES
for kind, x in (("start", o), ("upgrade", r), ("start", a), ("end", None)):
    for lang in ("en", "es"):
        t, b = S.message(kind, x, lang); ok(t and b and "spray" not in (t + b).lower() and "pulveri" not in (t + b).lower(), (t, b))
ok(S.message("start", o, "en") == ("⚠️ SMN orange alert: Storms", "San Alberto (Córdoba) · Mon 16 Nov 15:00 – Tue 17 Nov 02:59 ART. Tap for details."), S.message("start", o, "en"))
ok(S.message("start", o, "es")[0] == "⚠️ Alerta naranja del SMN: Tormentas", S.message("start", o, "es"))
ok(S.message("start", a, "en")[0] == "⛈️ SMN short-term warning (orange): severe storms", S.message("start", a, "en"))
# 5 end-to-end dry run of main() with the saved feed: never sends, writes the app file, dedupes across runs, 'ended' once
t = tempfile.mkdtemp(); os.makedirs(t + "/data")
env = {k: v for k, v in os.environ.items() if k not in ("VAPID_PRIVATE_KEY", "FB_BOT_PASSWORD")}
env.update(DRY_RUN="1", FEED_FILE=os.path.join(D, "feed.xml"), CAP_DIR=D, CAP_MAP=os.path.join(D, "urlmap.json"))
run = lambda now: subprocess.run([sys.executable, os.path.join(HERE, "smn_alerts.py")], cwd=t, env=dict(env, SIM_NOW=now), capture_output=True, text=True)
x = run("2026-11-16T16:30:00-03:00"); ok(x.returncode == 0 and x.stdout.count("would push") == 2, x.stdout + x.stderr)
out = json.load(open(t + "/data/smn-alerts.json")); ok([(z["kind"], z["level"]) for z in out["alerts"]] == [("alert", "orange"), ("short", "orange"), ("alert", "yellow")], out["alerts"])
x = run("2026-11-16T16:45:00-03:00"); ok("pushes: 0" in x.stdout, x.stdout)
x = run("2026-11-17T10:00:00-03:00"); ok("would push end" in x.stdout and json.load(open(t + "/data/smn-alerts.json"))["alerts"] == [], x.stdout)
x = run("2026-11-17T10:15:00-03:00"); ok("pushes: 0" in x.stdout, x.stdout)
print("smn_alerts: all checks passed")
