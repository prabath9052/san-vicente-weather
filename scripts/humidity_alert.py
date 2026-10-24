#!/usr/bin/env python3
"""Night humidity Web Push alert from the station's measured RH (sensor 20530638-2).
Fires when RH > 50% only between 18:00 and 06:00 (America/Argentina/Buenos_Aires).
Once per rise: re-armed when RH < 47%, and re-armed at the start of each night (18:00).
Recipients: alertNominees/{uid}.humidity == true (prabath9052 defaults to on unless set false). State: data/humidity-alert-state.json.
Secrets (env): VAPID_PRIVATE_KEY, FB_BOT_EMAIL, FB_BOT_PASSWORD.
Test env: SIM_NOW (ISO local time), SIM_RH, STATE_FILE, DRY_RUN=1 (no sending), ONLY_UID."""
import json, os, sys, datetime as dt, urllib.request, urllib.error, urllib.parse
KEY = "AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I"
FS = "https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
ON, OFF, START, END = 50.0, 47.0, 18, 6
TZ = dt.timezone(dt.timedelta(hours=-3))  # Argentina: UTC-3, no DST
ST = os.environ.get("STATE_FILE", "data/humidity-alert-state.json")
def req(url, data=None, tok=None, method=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method)
    return json.load(urllib.request.urlopen(r, timeout=30))
st = json.load(open("data/station.json"))
rh = float(os.environ["SIM_RH"]) if os.environ.get("SIM_RH") else st.get("rh")
now = dt.datetime.fromisoformat(os.environ["SIM_NOW"]) if os.environ.get("SIM_NOW") else dt.datetime.now(TZ)
now = now.astimezone(TZ); dry = os.environ.get("DRY_RUN") == "1"; only = os.environ.get("ONLY_UID")
if rh is None: print("no station RH reading"); sys.exit(0)
if not os.environ.get("SIM_NOW"):
    try:
        age = (now - dt.datetime.fromisoformat(st["observed"])).total_seconds() / 60
    except Exception: age = 0
    if age > 90: print(f"station reading is {age:.0f} min old; skipping"); sys.exit(0)
state = json.load(open(ST)) if os.path.exists(ST) else {"alerting": False}
night = now.hour >= START or now.hour < END
nid = (now.date() if now.hour >= START else now.date() - dt.timedelta(days=1)).isoformat() if night else None
if night and state.get("night") != nid:
    state["alerting"] = False; state["night"] = nid   # re-arm at 18:00
send = night and rh > ON and not state.get("alerting")
if night and rh > ON: state["alerting"] = True
elif rh < OFF: state["alerting"] = False
state.update(lastRh=rh, checked=now.isoformat(timespec="minutes"), observed=st.get("observed"))
if send: state["lastSent"] = state["checked"]
def save(): json.dump(state, open(ST, "w"), indent=1)
print(f"rh={rh}% night={night} alerting={state.get('alerting')} send={send}")
if not send or dry: save(); sys.exit(0)
if not os.environ.get("VAPID_PRIVATE_KEY") or not os.environ.get("FB_BOT_PASSWORD"):
    print("missing secrets; not sending"); state["alerting"] = False; state.pop("lastSent", None); save(); sys.exit(0)
from pywebpush import webpush, WebPushException
from py_vapid import Vapid01
try:
  tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
            {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
  VK = Vapid01.from_pem(os.environ["VAPID_PRIVATE_KEY"].encode())
  noms = req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
  hum = {d["name"].rsplit("/", 1)[1]: d.get("fields", {}).get("humidity", {}).get("booleanValue") for d in noms}
  # default: the admin (prabath9052) is on unless explicitly unticked
  adm = req(FS + "/usernames/prabath9052").get("fields", {}).get("authUid", {}).get("stringValue")
  if adm and hum.get(adm) is None: hum[adm] = True
  uids = [u for u, v in hum.items() if v]
  if only: uids = [u for u in uids if u == only]
  MSG = {"en": ("💧 Humidity alert", f"Station: {rh:.0f}% now (above 50%) · San Vicente"),
         "es": ("💧 Alerta de humedad", f"Estación: {rh:.0f}% ahora (sobre 50%) · San Vicente")}
  sent = 0
  for uid in uids:
      try: doc = req(f"{FS}/pushSubs/{uid}", tok=tok)
      except urllib.error.HTTPError as e:
          print(uid[:6], "no subs", e.code); continue
      f = doc.get("fields", {}); lang = "es" if f.get("lang", {}).get("stringValue") == "es" else "en"
      subs = f.get("subs", {}).get("mapValue", {}).get("fields", {}); dead = []
      title, body = MSG[lang]
      for k, v in subs.items():
          try:
              r = webpush(json.loads(v["stringValue"]), json.dumps({"title": title, "body": body, "tag": "humidity", "url": "./", "sound": "none"}),
                          vapid_private_key=VK, vapid_claims={"sub": "mailto:prabath9052@users.noreply.github.com"}, ttl=3600)
              sent += 1; print(uid[:6], urllib.parse.urlparse(json.loads(v["stringValue"])["endpoint"]).netloc, "HTTP", r.status_code)
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
