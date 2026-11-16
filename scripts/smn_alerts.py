#!/usr/bin/env python3
"""Official SMN (Servicio Meteorológico Nacional) alerts for San Vicente (San Alberto department, Córdoba).
Source: public SMN CAP RSS feed https://ssl.smn.gob.ar/CAP/AR.php (CC BY 4.0). Each item links to one CAP 1.2 XML:
  feeds/CAP/avisocortoplazo/...  short-term warnings (storms, ~1 h validity, 'AVISO AMARILLO/NARANJA/ROJO ...'),
                                 areaDesc lists 'PROVINCE: dept - dept.' plus a polygon;
  feeds/CAP/xml_generados/...    the yellow/orange/red alerts (one file per zone and time block, onset/expires,
                                 severity Moderate/Severe/Extreme), areaDesc empty, polygon only.
Matching: if areaDesc names departments -> 'SAN ALBERTO' under 'CORDOBA' (accent/case-insensitive) decides;
otherwise the station point (-31.8506, -65.4294) must be inside a polygon. Expired / cancelled items are ignored.
Known quirk: short-term warning times are UTC written with a '-03:00' suffix (file 2026_11_16_1903 = 16:03 ART);
when a short-term item is first seen with its 'sent' time in the future it is shifted by -3 h.
Output (only while something is active): data/smn-alerts.json -> Firestore private/smnAlerts (fs_sync) -> app banner.
Pushes: ONE per new alert. An updated/re-issued alert for the same event with an equal or lower level whose time
overlaps (or is within 1 h of) one already pushed is not pushed again; a higher level is ('upgrade').
When the last pushed alert has ended, one quiet 'SMN alerts ended' push. State: data/smn-state.json.
Recipients: alertNominees/{uid}.smn == true; the admin (prabath9052) is on unless set false.
Env: DRY_RUN=1 (never sends), SIM_NOW (ISO), FEED_FILE + CAP_DIR + CAP_MAP (offline tests), STATE_FILE, OUT_FILE, ONLY_UID,
     secrets VAPID_PRIVATE_KEY, FB_BOT_EMAIL, FB_BOT_PASSWORD (read only when a push is due)."""
import json, os, re, sys, unicodedata, datetime as dt, urllib.request, urllib.error, xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
TZ = dt.timezone(dt.timedelta(hours=-3))
FEED = "https://ssl.smn.gob.ar/CAP/AR.php"
LAT, LON = -31.8506, -65.4294
PROV, DEPT = "CORDOBA", "SAN ALBERTO"
ST = os.environ.get("STATE_FILE", "data/smn-state.json"); OUT = os.environ.get("OUT_FILE", "data/smn-alerts.json")
NS = "{urn:oasis:names:tc:emergency:cap:1.2}"
LEVELS = {"yellow": 1, "orange": 2, "red": 3}
SEV = {"Minor": "yellow", "Moderate": "yellow", "Severe": "orange", "Extreme": "red"}
EV_EN = {"TORMENTAS": "Storms", "TORMENTAS FUERTES": "Strong storms", "TORMENTAS SEVERAS": "Severe storms", "LLUVIAS": "Rain",
         "LLUVIAS INTENSAS": "Heavy rain", "VIENTO": "Wind", "VIENTO ZONDA": "Zonda wind", "NEVADAS": "Snow", "NIEVE": "Snow",
         "TEMPERATURAS EXTREMAS": "Extreme temperatures", "CALOR EXTREMO": "Extreme heat", "FRIO EXTREMO": "Extreme cold",
         "NIEBLA": "Fog", "POLVO EN SUSPENSION": "Blowing dust", "GRANIZO": "Hail", "CENIZA VOLCANICA": "Volcanic ash"}
COL = {"yellow": ("yellow", "amarilla"), "orange": ("orange", "naranja"), "red": ("red", "roja")}

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.upper()).strip()

def depts(area):
    """'LA RIOJA: GRAL BELGRANO - ROSARIO V.  PENALOZA. CORDOBA: MINAS.' -> {'LA RIOJA': [...], 'CORDOBA': ['MINAS']}"""
    a = norm(area); out = {}
    hs = list(re.finditer(r"(?:^|\.\s*)([A-Z][A-Z ]*?):", a))  # province names have no dots; department names may ("ROSARIO V. PENALOZA")
    for i, m in enumerate(hs):
        prov = m.group(1).strip(" ."); seg = a[m.end(): hs[i + 1].start() if i + 1 < len(hs) else len(a)]
        out.setdefault(prov, []).extend(d.strip(" .") for d in seg.split(" - ") if d.strip(" ."))
    return out

def inside(lat, lon, poly):
    ins = False
    for i in range(len(poly)):
        y1, x1 = poly[i]; y2, x2 = poly[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1: ins = not ins
    return ins

def ptime(s):
    try: return dt.datetime.fromisoformat(s.strip())
    except Exception: return None

def parse_feed(text):
    r = ET.fromstring(text.encode() if isinstance(text, str) else text); out = []
    for it in r.iter("item"):
        l = (it.findtext("link") or "").strip()
        if l: out.append({"title": (it.findtext("title") or "").strip(), "link": l, "desc": (it.findtext("description") or "").strip()})
    return out

def parse_cap(data, url):
    r = ET.fromstring(data); g = lambda e, k: (e.findtext(NS + k) or "").strip()
    info = r.find(NS + "info")
    if info is None: return None
    polys = []
    for p in info.iter(NS + "polygon"):
        try: polys.append([tuple(map(float, x.split(","))) for x in (p.text or "").split()])
        except ValueError: pass
    area = " ".join(g(a, "areaDesc") for a in info.iter(NS + "area")).strip()
    head = g(info, "headline"); hn = norm(head); sev = g(info, "severity")
    lvl = "red" if "ROJ" in hn else "orange" if "NARANJA" in hn else "yellow" if "AMARILL" in hn else SEV.get(sev, "yellow")
    sent, onset, exp = g(r, "sent"), g(info, "onset"), g(info, "expires")
    return {"id": g(r, "identifier"), "url": url, "kind": "short" if "/avisocortoplazo/" in url else "alert", "status": g(r, "status"),
            "msgType": g(r, "msgType"), "sent": sent, "onset": onset or sent, "expires": exp, "event": g(info, "event"), "severity": sev,
            "level": lvl, "headline": head, "description": g(info, "description"), "instruction": g(info, "instruction"),
            "areaDesc": area, "polys": polys, "web": g(info, "web")}

def fix_utc(a, now):
    """short-term warnings: wall time is UTC although labelled -03:00 -> shift when 'sent' is in the future when first seen"""
    s = ptime(a["sent"])
    if a["kind"] == "short" and s and s > now + dt.timedelta(minutes=15):
        for k in ("sent", "onset", "expires"):
            t = ptime(a[k])
            if t: a[k] = (t - dt.timedelta(hours=3)).isoformat()
        a["utcFix"] = True
    return a

def match(a):
    """-> 'name' | 'polygon' | None (area only; time is checked separately)"""
    d = depts(a["areaDesc"])
    if any(d.values()): return "name" if DEPT in d.get(PROV, []) else None
    return "polygon" if any(inside(LAT, LON, p) for p in a["polys"]) else None

def active(a, now):
    e = ptime(a["expires"])
    return a["status"] == "Actual" and a["msgType"] != "Cancel" and e is not None and e > now

def ev_en(e):
    n = norm(e); return EV_EN.get(n, e.capitalize() if e.isupper() else e)
def ev_es(e): return e.capitalize() if e.isupper() else e
def fmt(t, lang):
    t = ptime(t).astimezone(TZ)
    if lang == "es":
        d = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][t.weekday()]; m = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"][t.month - 1]
    else:
        d = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][t.weekday()]; m = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][t.month - 1]
    return f"{d} {t.day} {m} {t:%H:%M}"

def message(kind, a, lang):
    if kind == "end":
        return ("✅ SMN alerts ended", "No active SMN alerts for San Alberto (Córdoba) any more.") if lang == "en" else \
               ("✅ Terminaron las alertas del SMN", "Ya no hay alertas del SMN vigentes para San Alberto (Córdoba).")
    c = COL[a["level"]]; o, e = ptime(a["onset"]).astimezone(TZ), ptime(a["expires"]).astimezone(TZ)
    rng = f"{fmt(a['onset'], lang)}–{e:%H:%M} ART" if o.date() == e.date() else f"{fmt(a['onset'], lang)} – {fmt(a['expires'], lang)} ART"
    if a["kind"] == "short":
        t = f"⛈️ SMN short-term warning ({c[0]}): {ev_en(a['event']).lower()}" if lang == "en" else f"⛈️ Aviso a corto plazo del SMN ({c[1]}): {ev_es(a['event']).lower()}"
    else:
        up = kind == "upgrade"
        t = (f"⚠️ SMN {c[0]} alert{' (upgraded)' if up else ''}: {ev_en(a['event'])}" if lang == "en"
             else f"⚠️ Alerta {c[1]} del SMN{' (aumentó)' if up else ''}: {ev_es(a['event'])}")
    b = f"San Alberto (Córdoba) · {rng}. " + ("Tap for details." if lang == "en" else "Tocá para ver los detalles.")
    return t, b

def lkey(a): return norm(a["event"]).replace("FUERTES", "").replace("SEVERAS", "").strip() + "|" + a["kind"]

def step(state, alerts, now):
    """alerts: active matching alerts. Returns (state, pushes [(kind, alert)])."""
    state = dict(state or {}); sent = dict(state.get("sent", {})); pushes = []
    for a in sorted(alerts, key=lambda x: (x["onset"], -LEVELS[x["level"]])):
        if a["id"] in sent: continue
        o, e = ptime(a["onset"]), ptime(a["expires"]); cover = None
        for s in sent.values():
            if s["key"] == lkey(a) and ptime(s["onset"]) <= e + dt.timedelta(hours=1) and o <= ptime(s["expires"]) + dt.timedelta(hours=1):
                if cover is None or LEVELS[s["level"]] > LEVELS[cover["level"]]: cover = s
        kind = None if cover and LEVELS[cover["level"]] >= LEVELS[a["level"]] else "upgrade" if cover else "start"
        sent[a["id"]] = {"key": lkey(a), "level": a["level"], "onset": a["onset"], "expires": a["expires"], "pushed": bool(kind),
                         "at": now.isoformat(timespec="minutes")}
        if kind: pushes.append((kind, a))
    if not alerts and state.get("activeIds") and any(sent.get(i, {}).get("pushed") for i in state["activeIds"]) and not state.get("endSent"):
        pushes.append(("end", None)); state["endSent"] = True
    if alerts: state["endSent"] = False
    state["activeIds"] = sorted(a["id"] for a in alerts)
    state["sent"] = {k: v for k, v in sent.items() if ptime(v["expires"]) > now - dt.timedelta(days=2)}
    return state, pushes

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "san-vicente-weather (github.com/prabath9052/san-vicente-weather)"})
    return urllib.request.urlopen(req, timeout=timeout).read()

def load_sources(state, now):
    """-> (alerts matching + active, cache). Unchanged non-matching CAP files are not downloaded again."""
    if os.environ.get("FEED_FILE"):
        feed = parse_feed(open(os.environ["FEED_FILE"], "rb").read()); mp = json.load(open(os.environ["CAP_MAP"])) if os.environ.get("CAP_MAP") else {}
        get = lambda u: open(os.path.join(os.environ["CAP_DIR"], mp.get(u.rsplit("/", 1)[1], u.rsplit("/", 1)[1])), "rb").read()
    else: feed = parse_feed(fetch(FEED)); get = fetch
    cache = state.get("cache", {}); links = [i["link"] for i in feed]; new = [u for u in links if u not in cache]
    def one(u):
        try: return u, parse_cap(get(u), u)
        except Exception as e: print("smn: CAP failed", u.rsplit("/", 1)[1], type(e).__name__); return u, None
    with ThreadPoolExecutor(8) as ex: got = list(ex.map(one, new))
    for u, a in got:
        if a is None: continue
        a = fix_utc(a, now); m = match(a)
        cache[u] = dict(a, match=m, polys=None) if m else {"match": None, "expires": a["expires"]}
    cache = {u: v for u, v in cache.items() if u in links}
    alerts = [dict(v, url=u) for u, v in cache.items() if v.get("match") and active(v, now)]
    return alerts, cache

def send(pushes, dry):
    if not pushes: return 0
    if dry or not os.environ.get("VAPID_PRIVATE_KEY") or not os.environ.get("FB_BOT_PASSWORD"):
        for kind, a in pushes: print("smn: would push", kind, "|", *message(kind, a, "en"), "|", *message(kind, a, "es"))
        return 0
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid01
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from fs_sync import KEY, FS, req
    tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
              {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
    VK = Vapid01.from_pem(os.environ["VAPID_PRIVATE_KEY"].encode())
    noms = req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
    on = {d["name"].rsplit("/", 1)[1]: d.get("fields", {}).get("smn", {}).get("booleanValue") for d in noms}
    adm = req(FS + "/usernames/prabath9052").get("fields", {}).get("authUid", {}).get("stringValue")
    if adm and on.get(adm) is None: on[adm] = True
    uids = [u for u, v in on.items() if v]; only = os.environ.get("ONLY_UID")
    if only: uids = [u for u in uids if u == only]
    n = 0
    for uid in uids:
        try: doc = req(f"{FS}/pushSubs/{uid}", tok=tok)
        except urllib.error.HTTPError as e: print(uid[:6], "no subs", e.code); continue
        f = doc.get("fields", {}); lang = "es" if f.get("lang", {}).get("stringValue") == "es" else "en"
        subs = f.get("subs", {}).get("mapValue", {}).get("fields", {}); dead = []
        for kind, a in pushes:
            title, body = message(kind, a, lang); tag = "sv-smn-end" if kind == "end" else "sv-smn-" + re.sub(r"\W", "", a["id"])[-24:]
            ttl = 6 * 3600 if kind == "end" else max(600, int((ptime(a["expires"]) - dt.datetime.now(TZ)).total_seconds()))
            for sk, sv in subs.items():
                if sk in dead: continue
                try:
                    webpush(json.loads(sv["stringValue"]), json.dumps({"title": title, "body": body, "tag": tag, "type": "smn", "url": "./", "sound": "none"}),
                            vapid_private_key=VK, vapid_claims={"sub": "mailto:prabath9052@users.noreply.github.com"}, ttl=min(ttl, 24 * 3600)); n += 1
                except WebPushException as e:
                    code = e.response.status_code if e.response is not None else None; print(uid[:6], sk, "push failed", code)
                    if code in (404, 410): dead.append(sk)
        if dead:
            keep = {k: v for k, v in subs.items() if k not in dead}
            req(f"{FS}/pushSubs/{uid}?updateMask.fieldPaths=subs", {"fields": {"subs": {"mapValue": {"fields": keep}}}}, tok=tok, method="PATCH")
    print("smn: sent", n, "pushes to", len(uids), "users"); return n

def public(a):
    return {k: a.get(k) for k in ("id", "kind", "level", "event", "headline", "onset", "expires", "sent", "description", "instruction", "areaDesc", "url", "match", "severity")}

def main():
    now = dt.datetime.fromisoformat(os.environ["SIM_NOW"]) if os.environ.get("SIM_NOW") else dt.datetime.now(TZ)
    now = now.astimezone(TZ); dry = os.environ.get("DRY_RUN") == "1"
    try: state = json.load(open(ST))
    except Exception: state = {}
    try: alerts, cache = load_sources(state, now)
    except Exception as e:
        print("smn: feed failed:", type(e).__name__, getattr(e, "code", "")); return
    state["cache"] = cache
    before = json.dumps({k: v for k, v in state.items() if k != "cache"}, sort_keys=True)
    state, pushes = step(state, alerts, now)
    lv = lambda a: (-LEVELS[a["level"]], a["onset"])
    out = {"source": "SMN – Servicio Meteorológico Nacional (CC BY 4.0)", "feed": FEED, "area": "San Alberto, Córdoba",
           "alerts": [public(a) for a in sorted(alerts, key=lv)]}
    print("smn:", len(cache), "feed items,", len(alerts), "active for San Alberto", "|", ", ".join(f"{a['level']} {a['event']} until {a['expires'][:16]}" for a in sorted(alerts, key=lv)) or "none", "| pushes:", len(pushes))
    try: n = send(pushes, dry)
    except Exception as e: print("smn: send failed:", type(e).__name__, getattr(e, "code", "")); n = 0
    if n and not dry:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from alert_log import log_alert
            for kind, a in pushes:
                en, es = message(kind, a, "en"), message(kind, a, "es")
                log_alert(now.isoformat(timespec="minutes"), "smn", a["level"] if a else "end", en[0] + " – " + en[1], es[0] + " – " + es[1])
        except Exception as e: print("smn: log failed", e)
    old = None
    try: old = json.load(open(OUT))
    except Exception: pass
    if old is None or old.get("alerts") != out["alerts"]:
        out["updated"] = now.isoformat(timespec="minutes"); json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    json.dump(state, open(ST, "w"), ensure_ascii=False)
    if json.dumps({k: v for k, v in state.items() if k != "cache"}, sort_keys=True) != before: print("smn: state changed")
if __name__ == "__main__": main()
