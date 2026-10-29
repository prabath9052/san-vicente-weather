#!/usr/bin/env python3
"""Daily station summary EMAIL for the previous local day (America/Argentina/Cordoba, UTC-3), sent at 08:00 with the ET0 push.
Data: data/station-series.json hourly 'h' (t,tx,tn,rh,w,g,r,ss,e0) + data/station.json etYesterday (same ET0 as the push).
Recipients (Firestore alertNominees, read by the bot): any doc with summary == true and a valid 'email' field —
 user docs ({uid}, email entered by admin) and email-only docs ('email_<sha1(lower email)>', created in the admin panel).
 Optional field lang 'es'/'en' -> single language; otherwise bilingual (Spanish first).
Exactly once per day: data/eto-state.json 'summarySentDate' (independent of the push's lastSentDate; never touches it).
Never fails the workflow (always exit 0). Never prints secrets; recipient emails are masked in logs.
SMTP secrets (env): SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 465 = SSL; 587 = STARTTLS), SMTP_USER, SMTP_PASS, MAIL_FROM.
Modes: DRY_RUN=1 compute + print text only; MOCK_SMTP=<dir> write .eml files instead of sending (no Firestore needed with
 MOCK_TO=a@b,c@d); SUMMARY_DATE=YYYY-MM-DD (test modes only); TEST=1 send ONE mail to TEST_TO (or MAIL_FROM), no state change."""
import json, os, sys, time, hashlib, re, ssl, smtplib, statistics as stt, datetime as dt, urllib.request, urllib.error, html
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify import hourly_station
TZ = dt.timezone(dt.timedelta(hours=-3)); ST = os.environ.get("STATE_FILE", "data/eto-state.json")
E = os.environ.get; TEST = E("TEST") == "1"; DRY = E("DRY_RUN") == "1"; MOCK = E("MOCK_SMTP")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
def mask(a):
    u, _, d = a.partition("@"); return (u[:2] + "***@" + d[:1] + "***") if d else "***"
def calc(yd, now):
    ys = yd.isoformat(); H = hourly_station(["data/station-series.json"])
    rows = [H[k] for k in sorted(H) if k.startswith(ys)]
    if len(rows) < 22: return None
    g = lambda k: [r[k] for r in rows if r.get(k) is not None]
    r1 = lambda x, n=1: round(x, n) if x is not None else None
    t, w, rh = g("t"), g("w"), g("rh")
    S = {"date": ys, "hours": len(rows), "tmax": r1(max(g("tx") or t)), "tmin": r1(min(g("tn") or t)),
         "rain": r1(sum(g("r"))), "wmean": r1(stt.mean(w)) if w else None, "wmax": r1(max(w)) if w else None,
         "gust": r1(max(g("g"))) if g("g") else None, "rhmean": r1(stt.mean(rh), 0) if rh else None,
         "rhmin": r1(min(rh), 0) if rh else None, "sun": r1(sum(g("ss")) / 60) if g("ss") else None}
    et, src = None, "station"
    try:
        st = json.load(open("data/station.json")); f = dt.datetime.fromisoformat(st["observed"]).astimezone(TZ)
        if f.date() == now.date(): et = st.get("etYesterday")
    except Exception: pass
    if et is None and g("e0"): et, src = max(g("e0")), "computed"
    S["et0"], S["et0src"] = r1(et, 1), src
    # feels-like: heat index at the hottest hour (>= 27 °C), wind chill at the coldest hour (<= 10 °C, wind > 4.8 km/h)
    notes = []
    hot = max(rows, key=lambda r: r.get("t") if r.get("t") is not None else -99)
    if hot.get("t") is not None and hot["t"] >= 27 and hot.get("rh") is not None:
        T = hot["t"] * 9 / 5 + 32; R = hot["rh"]
        hi = -42.379 + 2.04901523*T + 10.14333127*R - .22475541*T*R - .00683783*T*T - .05481717*R*R + .00122874*T*T*R + .00085282*T*R*R - .00000199*T*T*R*R
        hi = (hi - 32) * 5 / 9
        if hi >= hot["t"] + 1: notes.append(("heat", round(hi)))
    cold = min(rows, key=lambda r: r.get("t") if r.get("t") is not None else 99)
    if cold.get("t") is not None and cold["t"] <= 10 and (cold.get("w") or 0) > 4.8:
        v = cold["w"] ** 0.16; wc = 13.12 + .6215*cold["t"] - 11.37*v + .3965*cold["t"]*v
        if wc <= cold["t"] - 1: notes.append(("chill", round(wc)))
    if S["tmin"] is not None and S["tmin"] <= 0: notes.append(("frost", S["tmin"]))
    elif S["tmin"] is not None and S["tmin"] < 4: notes.append(("gfrost", S["tmin"]))
    S["notes"] = notes; return S
L = {"es": {"tmax": "Temperatura máxima", "tmin": "Temperatura mínima", "rain": "Lluvia", "wmean": "Viento medio (2 m)",
            "wmax": "Viento máximo (2 m, media horaria)", "gust": "Ráfaga máxima", "rhmean": "Humedad media", "rhmin": "Humedad mínima",
            "et0": "ET0 de referencia (pasto)", "sun": "Horas de sol", "title": "Resumen del día", "na": "s/d",
            "heat": "Sensación térmica máx. ≈ {} °C (calor y humedad)", "chill": "Sensación térmica mín. ≈ {} °C (viento frío)",
            "frost": "❄ Helada: mínima {} °C", "gfrost": "❄ Posible helada en suelo/hojas: mínima {} °C (aire a 2 m)",
            "foot": "Estación meteorológica Fondomonte San Vicente. Datos de {} horas.", "unsub": "Para dejar de recibir este resumen, avisá al administrador."},
     "en": {"tmax": "Max temperature", "tmin": "Min temperature", "rain": "Rain", "wmean": "Mean wind (2 m)",
            "wmax": "Max wind (2 m, hourly mean)", "gust": "Max gust", "rhmean": "Mean humidity", "rhmin": "Min humidity",
            "et0": "Reference ET0 (grass)", "sun": "Sunshine hours", "title": "Daily summary", "na": "n/a",
            "heat": "Max feels-like ≈ {} °C (heat and humidity)", "chill": "Min feels-like ≈ {} °C (wind chill)",
            "frost": "❄ Frost: minimum {} °C", "gfrost": "❄ Possible ground/leaf frost: minimum {} °C (2 m air)",
            "foot": "Fondomonte San Vicente weather station. Based on {} hourly readings.", "unsub": "To stop receiving this summary, contact the administrator."}}
UNIT = {"tmax": "°C", "tmin": "°C", "rain": "mm", "wmean": "km/h", "wmax": "km/h", "gust": "km/h", "rhmean": "%", "rhmin": "%", "et0": "mm", "sun": "h"}
KEYS = ["tmax", "tmin", "rain", "wmean", "wmax", "gust", "rhmean", "rhmin", "et0", "sun"]
def fmt(S, k, lang):
    v = S.get(k)
    if v is None: return L[lang]["na"]
    s = f"{v:.0f}" if k in ("rhmean", "rhmin") else f"{v:.1f}"
    return (s.replace(".", ",") if lang == "es" else s) + " " + UNIT[k]
def section(S, lang, dd):
    t = L[lang]; rows = [(t[k], fmt(S, k, lang)) for k in KEYS]; notes = [t[n].format(str(v).replace(".", ",") if lang == "es" else v) for n, v in S["notes"]]
    txt = f"{t['title']} {dd}\n" + "\n".join(f"  {a}: {b}" for a, b in rows) + ("\n" + "\n".join("  " + n for n in notes) if notes else "") + "\n  " + t["foot"].format(S["hours"])
    h = (f'<h2 style="font:600 18px Arial,sans-serif;color:#1d3b2a;margin:18px 0 8px">{t["title"]} {dd}</h2>'
         '<table cellpadding="6" cellspacing="0" style="border-collapse:collapse;font:14px Arial,sans-serif;color:#222">'
         + "".join(f'<tr style="border-bottom:1px solid #e5e5e5"><td style="color:#555">{html.escape(a)}</td><td style="text-align:right;font-weight:600">{html.escape(b)}</td></tr>' for a, b in rows)
         + "</table>" + "".join(f'<p style="font:14px Arial,sans-serif;color:#0b4f8a;margin:8px 0">{html.escape(n)}</p>' for n in notes)
         + f'<p style="font:12px Arial,sans-serif;color:#888">{html.escape(t["foot"].format(S["hours"]))}</p>')
    return txt, h
def build(S, langs, test=False):
    d = dt.date.fromisoformat(S["date"]); dd = d.strftime("%d/%m")
    subj = f"San Vicente – resumen del día {dd}" if langs[0] == "es" else f"San Vicente – daily summary {dd}"
    if test: subj = "[TEST] " + subj
    parts = [section(S, l, dd) for l in langs]; unsub = " / ".join(L[l]["unsub"] for l in langs)
    txt = ("\n\n" + "-" * 40 + "\n\n").join(p[0] for p in parts) + "\n\n" + unsub + "\n"
    hh = ('<div style="max-width:560px;margin:0 auto;padding:8px 14px">' + '<hr style="border:0;border-top:1px solid #ccc;margin:20px 0">'.join(p[1] for p in parts)
          + f'<p style="font:12px Arial,sans-serif;color:#888;margin-top:20px">{html.escape(unsub)}</p>'
          + '<p style="font:12px Arial,sans-serif"><a href="https://prabath9052.github.io/san-vicente-weather/">San Vicente Weather</a></p></div>')
    return subj, txt, hh
def recipients():
    if E("MOCK_TO"): return [(a.strip(), None) for a in E("MOCK_TO").split(",") if a.strip()]
    from eto_daily_fs import token, list_nominees
    out, seen = [], set()
    for d in list_nominees(token()):
        f = d.get("fields", {}); em = (f.get("email", {}).get("stringValue") or "").strip()
        if f.get("summary", {}).get("booleanValue") is True and EMAIL_RE.match(em) and em.lower() not in seen:
            seen.add(em.lower()); lg = f.get("lang", {}).get("stringValue"); out.append((em, lg if lg in ("es", "en") else None))
    return out
def send(msgs):
    if MOCK:
        os.makedirs(MOCK, exist_ok=True)
        for i, m in enumerate(msgs): open(os.path.join(MOCK, f"mail{i}.eml"), "wb").write(bytes(m))
        print("MOCK_SMTP: wrote", len(msgs), "messages to", MOCK); return len(msgs)
    host, port = E("SMTP_HOST") or "smtp.gmail.com", int(E("SMTP_PORT") or 465)
    ctx = ssl.create_default_context(); n = 0
    s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
    try:
        if port != 465: s.starttls(context=ctx)
        s.login(E("SMTP_USER"), E("SMTP_PASS"))
        for m in msgs:
            try: s.send_message(m); n += 1; print("sent to", mask(m["To"]))
            except smtplib.SMTPException as e: print("send failed to", mask(m["To"]), type(e).__name__)
    finally:
        try: s.quit()
        except Exception: pass
    return n
def main():
    now = dt.datetime.now(TZ)
    if not (TEST or DRY or MOCK) and now.hour < 8: print("summary: before 08:00 local; skipping"); return
    yd = now.date() - dt.timedelta(days=1)
    if E("SUMMARY_DATE") and (TEST or DRY or MOCK): yd = dt.date.fromisoformat(E("SUMMARY_DATE"))  # testing only
    ys = yd.isoformat()
    state = json.load(open(ST)) if os.path.exists(ST) else {}
    if not (TEST or DRY or MOCK) and state.get("summarySentDate") == ys: print("summary: already sent for", ys); return
    S = calc(yd, now)
    if not S: print("summary: < 22 station hours for", ys, "; skipping (retry next run)"); return
    if DRY:
        print(build(S, ["es", "en"])[1]); return
    if not MOCK and not (E("SMTP_USER") and E("SMTP_PASS")):
        print("summary: SMTP secrets missing (need SMTP_USER, SMTP_PASS; optional SMTP_HOST, SMTP_PORT, MAIL_FROM) - email skipped"); return
    frm = E("MAIL_FROM") or E("SMTP_USER") or "noreply@example.com"
    if TEST: rc = [((E("TEST_TO") or frm).strip(), None)]
    else: rc = recipients()
    rc = [r for r in rc if EMAIL_RE.match(r[0])]
    print("summary:", ys, "recipients", len(rc), [mask(a) for a, _ in rc])
    if not rc: print("summary: no recipients"); return
    msgs = []
    for a, lg in rc:
        subj, txt, hh = build(S, [lg] if lg else ["es", "en"], TEST)
        m = EmailMessage(); m["Subject"] = subj; m["From"] = formataddr(("San Vicente Weather", frm)); m["To"] = a
        m["Message-ID"] = make_msgid(domain="san-vicente-weather"); m.set_content(txt); m.add_alternative(hh, subtype="html"); msgs.append(m)
    n = send(msgs); print("summary: sent", n, "of", len(msgs), "(TEST)" if TEST else "(MOCK)" if MOCK else "")
    if TEST or MOCK or n == 0: return
    state = json.load(open(ST)) if os.path.exists(ST) else {}  # re-read: never clobber the push's lastSentDate
    state.update(summarySentDate=ys, summarySentAt=now.isoformat(timespec="minutes"), summarySent=n)
    json.dump(state, open(ST, "w"), indent=1)
if __name__ == "__main__":
    try: main()
    except Exception as e: print("summary failed:", type(e).__name__)  # never block the push / workflow
