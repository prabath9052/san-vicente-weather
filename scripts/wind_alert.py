#!/usr/bin/env python3
"""Send a Web Push wind alert to nominated users when the station's CURRENT average wind
rises above 15 km/h. Once per rise; re-armed when wind drops below 13 km/h (hysteresis).
State: data/wind-alert-state.json. Secrets (env): VAPID_PRIVATE_KEY, FB_BOT_EMAIL, FB_BOT_PASSWORD.
Test env: FORCE_THRESHOLD (km/h, ignores/does not change state), ONLY_UID (send only to that uid)."""
import json, os, sys, datetime as dt, urllib.request, urllib.error
from pywebpush import webpush, WebPushException
from py_vapid import Vapid01
KEY = "AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I"
FS = "https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
ON, OFF = 15.0, 13.0
ST = "data/wind-alert-state.json"
def req(url, data=None, tok=None, method=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method)
    return json.load(urllib.request.urlopen(r, timeout=30))
st = json.load(open("data/station.json"))
wind, gust = st.get("wind"), st.get("gust")
force = os.environ.get("FORCE_THRESHOLD"); only = os.environ.get("ONLY_UID")
state = json.load(open(ST)) if os.path.exists(ST) else {"alerting": False}
# Station only: 'wind' is the HOBOlink anemometer's latest measured average (sensor 21796781-1), never forecast data.
if wind is None: print("no station wind reading"); sys.exit(0)
try:
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(st["observed"])).total_seconds() / 60
except Exception: age = 0
if age > 90 and not force: print(f"station reading is {age:.0f} min old; skipping"); sys.exit(0)
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).isoformat(timespec="minutes")
if force:
    send = wind > float(force)
else:
    send = wind > ON and not state.get("alerting")
    if wind > ON: state["alerting"] = True
    elif wind < OFF: state["alerting"] = False
    state.update(lastWind=wind, checked=now, observed=st.get("observed"))
    if send: state["lastSent"] = now
def save():
    if not force: json.dump(state, open(ST, "w"), indent=1)
print(f"wind={wind} km/h alerting={state.get('alerting')} send={send}")
if not send: save(); sys.exit(0)
if not os.environ.get("VAPID_PRIVATE_KEY") or not os.environ.get("FB_BOT_PASSWORD"):
    print("missing secrets; not sending"); state["alerting"] = False; state.pop("lastSent", None); save(); sys.exit(0)
try:
  tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
            {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
  VK = Vapid01.from_pem(os.environ["VAPID_PRIVATE_KEY"].encode())
  noms = req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
  uids = [d["name"].rsplit("/", 1)[1] for d in noms if d.get("fields", {}).get("wind", {}).get("booleanValue")]
  if only: uids = [u for u in uids if u == only] or ([only] if force else [])
  g = f"{gust:.0f}" if isinstance(gust, (int, float)) else "—"
  MSG = {"en": ("💨 Windy for spraying", f"Station: {wind:.0f} km/h now (gust {g}) · San Vicente"),
         "es": ("💨 Viento para pulverizar", f"Estación: {wind:.0f} km/h ahora (ráfaga {g}) · San Vicente")}
  sent = 0
  for uid in uids:
      try: doc = req(f"{FS}/pushSubs/{uid}", tok=tok)
      except urllib.error.HTTPError as e:
          print(uid[:6], "no subs", e.code); continue
      f = doc.get("fields", {}); lang = f.get("lang", {}).get("stringValue", "en"); lang = "es" if lang == "es" else "en"
      subs = f.get("subs", {}).get("mapValue", {}).get("fields", {}); dead = []
      title, body = MSG[lang]
      for k, v in subs.items():
          try:
              webpush(json.loads(v["stringValue"]), json.dumps({"title": title, "body": body, "tag": "sv-wind", "url": "./", "sound": "wind-gust.mp3"}),
                      vapid_private_key=VK, vapid_claims={"sub": "mailto:prabath9052@users.noreply.github.com"}, ttl=3600)
              sent += 1
          except WebPushException as e:
              code = e.response.status_code if e.response is not None else None
              print(uid[:6], k, "push failed", code)
              if code in (404, 410): dead.append(k)
      if dead:
          keep = {k: v for k, v in subs.items() if k not in dead}
          req(f"{FS}/pushSubs/{uid}?updateMask.fieldPaths=subs", {"fields": {"subs": {"mapValue": {"fields": keep}}}}, tok=tok, method="PATCH")
          print(uid[:6], "removed", len(dead), "expired")
  print("sent", sent, "to", len(uids), "users")

except Exception as e:
  print("send failed:", type(e).__name__, getattr(e, "code", ""))
  state["alerting"] = False; state.pop("lastSent", None); save(); sys.exit(1)
save()
