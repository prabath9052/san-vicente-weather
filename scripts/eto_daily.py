#!/usr/bin/env python3
"""Daily 08:00 (America/Argentina/Cordoba, UTC-3) Web Push: YESTERDAY's station reference ET0 (+ rain).
ET0 = the station's own daily reference ET for yesterday (data/station.json 'etYesterday', the app's
'Reference ET yesterday (station)' tile); fallback = day max of the computed cumulative e0 series (as verify.py).
Rain = sum of yesterday's hourly station rain (needs >= 22 station hours).
Recipients: alertNominees/{uid}.eto == true (prabath9052 on unless set false).
Exactly once per day: state data/eto-state.json {lastSentDate} (mirrored to Firestore private/etoState by fs_sync).
WAIT_UNTIL_8=1: sleep until 08:00 local (max 90 min) before sending. Before 08:00 without waiting -> skip.
Test: TEST=1 sends ONE message to prabath9052 only, no state change, no alertsLog entry. DRY_RUN=1: compute only.
Secrets (env): VAPID_PRIVATE_KEY, FB_BOT_EMAIL, FB_BOT_PASSWORD."""
import json, os, sys, time, datetime as dt, urllib.request, urllib.error, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify import hourly_station, obs_day
KEY = "AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I"
FS = "https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
TZ = dt.timezone(dt.timedelta(hours=-3)); ST = os.environ.get("STATE_FILE", "data/eto-state.json")
TEST = os.environ.get("TEST") == "1"; DRY = os.environ.get("DRY_RUN") == "1"
DOW = {"en": "Mon Tue Wed Thu Fri Sat Sun".split(), "es": "lun mar mié jue vie sáb dom".split()}
MON = {"en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), "es": "ene feb mar abr may jun jul ago sep oct nov dic".split()}
def req(url, data=None, tok=None, method=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method)
    return json.load(urllib.request.urlopen(r, timeout=30))
now = dt.datetime.now(TZ)
if os.environ.get("WAIT_UNTIL_8") == "1" and not TEST:
    t8 = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now < t8 and (t8 - now).total_seconds() <= 90 * 60:
        print("waiting", int((t8 - now).total_seconds()), "s until 08:00 local"); time.sleep((t8 - now).total_seconds()); now = dt.datetime.now(TZ)
if not TEST and now.hour < 8: print("before 08:00 local; skipping"); sys.exit(0)
yd = (now.date() - dt.timedelta(days=1)); ys = yd.isoformat()
state = json.load(open(ST)) if os.path.exists(ST) else {}
if not TEST and state.get("lastSentDate") == ys: print("already sent for", ys); sys.exit(0)
try: st = json.load(open("data/station.json"))
except Exception: print("no station.json; skipping"); sys.exit(0)
try: fetched = dt.datetime.fromisoformat(st["observed"])
except Exception: fetched = None
# etYesterday = day before the reading's local midnight: require a reading from today (local)
et = st.get("etYesterday") if fetched and fetched.astimezone(TZ).date() == now.date() else None
od = obs_day(hourly_station(["data/station-series.json"]), ys)
src = "station"
if et is None and od and od.get("et0") is not None: et, src = od["et0"], "computed"
rain = od.get("rain") if od else None
print(f"{ys}: ET0={et} ({src}) computed={od and od.get('et0')} rain={rain} hours={od and od.get('hours')} observed={st.get('observed')}")
if et is None: print("ET0 for yesterday unavailable/stale; skipping (will retry next run)"); sys.exit(0)
def body(lang):
    d = f"{DOW[lang][yd.weekday()]} {yd.day} {MON[lang][yd.month-1]}"
    r = f" · {'lluvia' if lang=='es' else 'rain'} {rain:.1f} mm" if rain is not None else ""
    return f"{d}: ET0 {et:.1f} mm{r}"
TITLE = {"en": "🌱 Reference ET0 yesterday", "es": "🌱 ET0 de referencia de ayer"}
print("message:", TITLE["en"], "|", body("en"))
if DRY: sys.exit(0)
if not os.environ.get("VAPID_PRIVATE_KEY") or not os.environ.get("FB_BOT_PASSWORD"): print("missing secrets; not sending"); sys.exit(0)
from pywebpush import webpush, WebPushException
from py_vapid import Vapid01
tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
          {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
VK = Vapid01.from_pem(os.environ["VAPID_PRIVATE_KEY"].encode())
adm = req(FS + "/usernames/prabath9052").get("fields", {}).get("authUid", {}).get("stringValue")
if TEST: uids = [adm] if adm else []
else:
    noms = req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
    eto = {d["name"].rsplit("/", 1)[1]: d.get("fields", {}).get("eto", {}).get("booleanValue") for d in noms}
    if adm and eto.get(adm) is None: eto[adm] = True
    uids = [u for u, v in eto.items() if v]
sent = 0
for uid in uids:
    try: doc = req(f"{FS}/pushSubs/{uid}", tok=tok)
    except urllib.error.HTTPError as e: print(uid[:6], "no subs", e.code); continue
    f = doc.get("fields", {}); lang = "es" if f.get("lang", {}).get("stringValue") == "es" else "en"
    subs = f.get("subs", {}).get("mapValue", {}).get("fields", {}); dead = []
    for k, v in subs.items():
        try:
            r = webpush(json.loads(v["stringValue"]), json.dumps({"title": TITLE[lang] + (" (test)" if TEST else ""), "body": body(lang), "tag": "eto", "url": "./", "sound": "none"}),
                        vapid_private_key=VK, vapid_claims={"sub": "mailto:prabath9052@users.noreply.github.com"}, ttl=6 * 3600)
            sent += 1; print(uid[:6], urllib.parse.urlparse(json.loads(v["stringValue"])["endpoint"]).netloc, "HTTP", r.status_code)
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else None
            print(uid[:6], k, "push failed", code)
            if code in (404, 410): dead.append(k)
    if dead and not TEST:
        keep = {k: v for k, v in subs.items() if k not in dead}
        req(f"{FS}/pushSubs/{uid}?updateMask.fieldPaths=subs", {"fields": {"subs": {"mapValue": {"fields": keep}}}}, tok=tok, method="PATCH")
print("sent", sent, "to", len(uids), "users", "(TEST)" if TEST else "")
if TEST: sys.exit(0)
state.update(lastSentDate=ys, sentAt=now.isoformat(timespec="minutes"), et0=et, rain=rain, src=src, sent=sent)
json.dump(state, open(ST, "w"), indent=1)
from alert_log import log_alert
log_alert(now.isoformat(timespec="minutes"), "eto", et, body("en"), body("es"), None, st.get("observed"))
