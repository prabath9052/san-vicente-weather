#!/usr/bin/env python3
"""Station health check (runs on every pass of the scheduled station workflow, after the HOBOlink fetch).
Problems detected
  offline  - no new reading for more than OFFLINE_MIN (120 min). The logger records every 5 min and uploads every
             10 min, and the workflow polls ~hourly for ~27 min, so 2 h means many missed uploads, not a short delay.
  sensors  - only while the station is online, from the latest reading and the last 3 h of 15-min buckets:
             air temp outside -15..50 C, soil temp outside -10..70 C, RH < 1 % or > 100 %, dew point > air temp + 1 C,
             wind/gust < 0 or > 150 km/h, rain < 0 or > 60 mm in 15 min, solar < -5 or > 1500 W/m2,
             solar > 30 W/m2 with the sun more than 3 deg below the horizon,
             air temp / RH (below 99 %) / non-zero wind stuck on exactly the same value for 6 h (24 buckets),
             a main sensor (air temp, RH, wind, solar) missing from the latest reading.
State/dedupe (data/health-state.json, mirrored to Firestore private/healthState so the app shows the banner):
  a sensor problem must be seen on 2 checks at least 20 min apart before it is reported (one-off glitches ignored);
  each problem is pushed once when it starts and once when it clears ('back online' / 'sensor OK again');
  the same problem is not pushed again within COOLDOWN_H (6 h) of its last push (flapping).
Recipients: alertNominees/{uid}.health == true; the admin (prabath9052) is on unless set false.
Env: DRY_RUN=1 (never sends; prints what it would send), SIM_NOW (ISO local time), STATE_FILE, ONLY_UID,
     secrets VAPID_PRIVATE_KEY, FB_BOT_EMAIL, FB_BOT_PASSWORD (only read when a push is due)."""
import json, os, sys, math, datetime as dt, urllib.request, urllib.error
TZ = dt.timezone(dt.timedelta(hours=-3))
OFFLINE_MIN, CONFIRM_MIN, COOLDOWN_H, STUCK_N = 120, 20, 6, 24
LAT, LON = -31.8506, -65.4294
ST = os.environ.get("STATE_FILE", "data/health-state.json")
NAMES = {"offline": ("Station data", "Datos de la estación"), "temp": ("Air temperature", "Temperatura del aire"),
         "soil": ("Soil temperature", "Temperatura del suelo"), "rh": ("Humidity", "Humedad"), "dew": ("Dew point", "Punto de rocío"),
         "wind": ("Wind", "Viento"), "rain": ("Rain gauge", "Pluviómetro"), "solar": ("Solar radiation", "Radiación solar")}

def _n(x): return isinstance(x, (int, float)) and x == x
def sun_el(t):
    """solar elevation (deg) at the station for an aware datetime"""
    d = t.timestamp() / 86400 + 2440587.5 - 2451545; g = math.radians(357.529 + .98560028 * d); q = 280.459 + .98564736 * d
    L = math.radians(q + 1.915 * math.sin(g) + .02 * math.sin(2 * g)); e = math.radians(23.439 - 3.6e-7 * d)
    ra = math.atan2(math.cos(e) * math.sin(L), math.cos(L)); dec = math.asin(math.sin(e) * math.sin(L))
    gm = ((18.697374558 + 24.06570982441908 * d) % 24 + 24) % 24; ha = math.radians(gm * 15 + LON) - ra; la = math.radians(LAT)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))

def evaluate(st, ser, now):
    """Return {key: {"en": text, "es": text, "value": ...}} of problems right now (pure; no state)."""
    P = {}
    try: obs = dt.datetime.fromisoformat(st["observed"])
    except Exception: obs = None
    age = (now - obs).total_seconds() / 60 if obs else None
    if age is None or age > OFFLINE_MIN:
        hh = obs.astimezone(TZ).strftime("%d/%m %H:%M") if obs else "—"; h = f"{age / 60:.0f} h" if age else "—"
        P["offline"] = {"en": f"No new data from the station since {hh} ART ({h})", "es": f"Sin datos nuevos de la estación desde el {hh} ART ({h})", "value": round(age) if age else None, "since": st.get("observed")}
        return P  # sensor checks are meaningless on stale data
    def add(k, en, es, v):
        if k not in P: P[k] = {"en": f"{NAMES[k][0]}: {en}", "es": f"{NAMES[k][1]}: {es}", "value": v}
    cur = {"t": st.get("temp"), "rh": st.get("rh"), "dp": st.get("dewPoint"), "w": st.get("wind"), "g": st.get("gust"), "sr": st.get("solar"), "st": st.get("soilTemp")}
    for k, nm in (("t", "temp"), ("rh", "rh"), ("w", "wind"), ("sr", "solar")):
        if cur[k] is None: add(nm, "no reading", "sin lectura", None)
    q = (ser or {}).get("q") or {}; v = q.get("v") or {}; n = q.get("n", 0); t0, step = q.get("t0"), q.get("step", 900000)
    last = [i for i in range(n) if t0 is not None and t0 + i * step <= now.timestamp() * 1000][-12:] if n else []  # last 3 h
    col = lambda k: [(i, v.get(k, [None] * n)[i]) for i in last if i < len(v.get(k, [])) and _n(v.get(k)[i])]
    vals = lambda k, c: ([c] if _n(c) else []) + [x for _, x in col(k)]
    for x in vals("t", cur["t"]):
        if not -15 <= x <= 50: add("temp", f"{x:.1f} °C (impossible)", f"{x:.1f} °C (imposible)", x); break
    for x in vals("st", cur["st"]):
        if not -10 <= x <= 70: add("soil", f"{x:.1f} °C (impossible)", f"{x:.1f} °C (imposible)", x); break
    for x in vals("rh", cur["rh"]):
        if x < 1 or x > 100: add("rh", f"{x:.0f} % (impossible)", f"{x:.0f} % (imposible)", x); break
    if _n(cur["dp"]) and _n(cur["t"]) and cur["dp"] > cur["t"] + 1: add("dew", f"{cur['dp']:.1f} °C, above the air temperature", f"{cur['dp']:.1f} °C, sobre la temperatura del aire", cur["dp"])
    for x in vals("w", cur["w"]) + vals("g", cur["g"]):
        if x < 0 or x > 150: add("wind", f"{x:.0f} km/h (impossible)", f"{x:.0f} km/h (imposible)", x); break
    for _, x in col("r"):
        if x < 0 or x > 60: add("rain", f"{x:.1f} mm in 15 min (impossible)", f"{x:.1f} mm en 15 min (imposible)", x); break
    for x in vals("sr", cur["sr"]):
        if x < -5 or x > 1500: add("solar", f"{x:.0f} W/m² (impossible)", f"{x:.0f} W/m² (imposible)", x); break
    for i, x in col("sr"):
        mid = dt.datetime.fromtimestamp((t0 + i * step + step / 2) / 1000, TZ)
        if x > 30 and sun_el(mid) < -3: add("solar", f"{x:.0f} W/m² at night", f"{x:.0f} W/m² de noche", x); break
    if n and t0 is not None:
        idx = [i for i in range(n) if t0 + i * step <= now.timestamp() * 1000][-STUCK_N:]
        for k, nm, ok in (("t", "temp", lambda x: True), ("rh", "rh", lambda x: x < 99), ("w", "wind", lambda x: x > 0)):
            s = [v.get(k)[i] if i < len(v.get(k, [])) else None for i in idx]
            if len(s) == STUCK_N and all(_n(x) for x in s) and max(s) == min(s) and ok(s[0]):
                add(nm, f"stuck at {s[0]} for 6 h", f"fija en {s[0]} durante 6 h", s[0])
    return P

def step(state, probs, now):
    """Update state with the current problems; return (state, pushes). pushes: [(kind 'start'|'end', key, info)]."""
    S = state.setdefault("issues", {}); out = []; ts = now.isoformat(timespec="minutes")
    def cool(x): return x.get("pushed") and (now - dt.datetime.fromisoformat(x["pushed"])).total_seconds() < COOLDOWN_H * 3600
    for k, info in probs.items():
        x = S.get(k)
        if not x or x.get("cleared"): x = S[k] = {"first": ts, "seen": 1, "active": False, "pushed": (x or {}).get("pushed")}
        else: x["seen"] = min(2, x.get("seen", 1) + 1)   # capped so the stored state only changes on real transitions
        x.update(en=info["en"], es=info["es"])
        confirmed = k == "offline" or (now - dt.datetime.fromisoformat(x["first"])).total_seconds() >= CONFIRM_MIN * 60 and x["seen"] >= 2
        if confirmed and not x["active"]:
            x["active"] = True; x["since"] = info.get("since") or x["first"]
            if not cool(x): x["pushed"] = ts; x["notified"] = True; out.append(("start", k, x))
            else: x["notified"] = False
    for k in list(S):
        if k in probs: continue
        x = S[k]
        if x.get("active"):
            x.update(active=False, cleared=ts)
            if x.get("notified"): out.append(("end", k, x)); x["pushed"] = ts
        elif not x.get("cleared"): S.pop(k)   # never confirmed: forget the glitch
        elif (now - dt.datetime.fromisoformat(x["cleared"])).total_seconds() > 2 * 86400: S.pop(k)
    state["ok"] = not any(x.get("active") for x in S.values())
    state["active"] = {k: {"en": x["en"], "es": x["es"], "since": x.get("since")} for k, x in S.items() if x.get("active")}
    return state, out

def message(kind, k, x, lang):
    es = lang == "es"
    if kind == "start":
        if k == "offline": return ("📡 Estación sin datos" if es else "📡 Station offline"), x["es" if es else "en"] + " · San Vicente"
        return ("⚠️ Revisión de sensor" if es else "⚠️ Station sensor check"), x["es" if es else "en"] + " · San Vicente"
    if k == "offline": return ("✅ Estación en línea de nuevo" if es else "✅ Station back online"), ("Llegan datos nuevos otra vez" if es else "New data is arriving again") + " · San Vicente"
    return ("✅ Sensor normal de nuevo" if es else "✅ Sensor OK again"), NAMES[k][1 if es else 0] + (" · lecturas normales · San Vicente" if es else " · readings back to normal · San Vicente")

def send(pushes, dry):
    if not pushes: return 0
    if dry or not os.environ.get("VAPID_PRIVATE_KEY") or not os.environ.get("FB_BOT_PASSWORD"):
        for kind, k, x in pushes: print("health: would push", kind, k, "|", *message(kind, k, x, "en"))
        return 0
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid01
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from fs_sync import KEY, FS, req
    tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
              {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
    VK = Vapid01.from_pem(os.environ["VAPID_PRIVATE_KEY"].encode())
    noms = req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
    on = {d["name"].rsplit("/", 1)[1]: d.get("fields", {}).get("health", {}).get("booleanValue") for d in noms}
    adm = req(FS + "/usernames/prabath9052").get("fields", {}).get("authUid", {}).get("stringValue")
    if adm and on.get(adm) is None: on[adm] = True
    uids = [u for u, v in on.items() if v]; only = os.environ.get("ONLY_UID")
    if only: uids = [u for u in uids if u == only]
    sent = 0
    for uid in uids:
        try: doc = req(f"{FS}/pushSubs/{uid}", tok=tok)
        except urllib.error.HTTPError as e: print(uid[:6], "no subs", e.code); continue
        f = doc.get("fields", {}); lang = "es" if f.get("lang", {}).get("stringValue") == "es" else "en"
        subs = f.get("subs", {}).get("mapValue", {}).get("fields", {}); dead = []
        for kind, k, x in pushes:
            title, body = message(kind, k, x, lang)
            for sk, sv in subs.items():
                if sk in dead: continue
                try:
                    webpush(json.loads(sv["stringValue"]), json.dumps({"title": title, "body": body, "tag": "sv-health-" + k, "url": "./", "sound": "none"}),
                            vapid_private_key=VK, vapid_claims={"sub": "mailto:prabath9052@users.noreply.github.com"}, ttl=6 * 3600); sent += 1
                except WebPushException as e:
                    code = e.response.status_code if e.response is not None else None; print(uid[:6], sk, "push failed", code)
                    if code in (404, 410): dead.append(sk)
        if dead:
            keep = {a: b for a, b in subs.items() if a not in dead}
            req(f"{FS}/pushSubs/{uid}?updateMask.fieldPaths=subs", {"fields": {"subs": {"mapValue": {"fields": keep}}}}, tok=tok, method="PATCH")
    print("health: sent", sent, "pushes to", len(uids), "users"); return sent

def main():
    now = dt.datetime.fromisoformat(os.environ["SIM_NOW"]) if os.environ.get("SIM_NOW") else dt.datetime.now(TZ)
    now = now.astimezone(TZ); dry = os.environ.get("DRY_RUN") == "1"
    try: st = json.load(open("data/station.json"))
    except Exception: st = {}
    try: ser = json.load(open("data/station-series.json"))
    except Exception: ser = {}
    try: state = json.load(open(ST))
    except Exception: state = {}
    before = json.dumps(state, sort_keys=True)
    probs = evaluate(st, ser, now); state, pushes = step(state, probs, now)
    print("health:", "ok" if not probs else "problems: " + ", ".join(probs), "| active:", ", ".join(state["active"]) or "none", "| pushes:", len(pushes))
    try: n = send(pushes, dry)
    except Exception as e: print("health: send failed:", type(e).__name__, getattr(e, "code", "")); n = 0
    if n and not dry:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from alert_log import log_alert
            for kind, k, x in pushes:
                en, es = message(kind, k, x, "en"), message(kind, k, x, "es")
                log_alert(now.isoformat(timespec="minutes"), "health", k, en[0] + " – " + en[1], es[0] + " – " + es[1], None, st.get("observed"))
        except Exception as e: print("health: log failed", e)
    if json.dumps(state, sort_keys=True) != before or not os.path.exists(ST): json.dump(state, open(ST, "w"), ensure_ascii=False, indent=1)
if __name__ == "__main__": main()
