#!/usr/bin/env python3
"""Daily station summary EMAIL for the previous local day (America/Argentina/Cordoba, UTC-3), sent at 08:00 with the ET0 push.
Data: data/station-series.json hourly 'h' (t,tx,tn,rh,w,g,r,ss,e0) + data/station.json etYesterday (same ET0 as the push).
Recipients (Firestore alertNominees, read by the bot): any doc with summary == true and a valid 'email' field —
 user docs ({uid}, email entered by admin) and email-only docs ('email_<sha1(lower email)>', created in the admin panel).
 Optional field lang 'es'/'en' -> single language; otherwise bilingual (English first, Spanish second).
Exactly once per day: data/eto-state.json 'summarySentDate' (independent of the push's lastSentDate; never touches it).
Never fails the workflow (always exit 0). Never prints secrets; recipient emails are masked in logs.
SMTP secrets (env): SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 465 = SSL; 587 = STARTTLS), SMTP_USER, SMTP_PASS, MAIL_FROM.
Modes: DRY_RUN=1 compute + print text only; MOCK_SMTP=<dir> write .eml files instead of sending (no Firestore needed with
 MOCK_TO=a@b,c@d); SUMMARY_DATE=YYYY-MM-DD (test modes only); PREVIEW_HTML=<file> (with DRY_RUN) saves the HTML; TEST=1 send ONE mail to TEST_TO (or MAIL_FROM), no state change."""
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
DOWN = {"es": "lunes martes miércoles jueves viernes sábado domingo".split(), "en": "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()}
TX = {"es": {"day": "Resumen del día", "rainw": "lluvia", "temp": "Temperatura máx. / mín.", "rain": "Lluvia", "et0": "ET0 de referencia", "wind": "Viento (2 m)",
             "maxmin": "máx / mín", "mean": "media", "max": "máx", "gust": "ráfaga", "grass": "pasto", "hum": "Humedad media / mínima", "sun": "Horas de sol",
             "spray_ok": "Bueno para pulverizar", "spray_no": "Ventoso para pulverizar", "src": "Datos de la estación San Vicente, San Alberto, Córdoba", "gen": "Generado"},
      "en": {"day": "Daily summary", "rainw": "rain", "temp": "Temperature max / min", "rain": "Rain", "et0": "Reference ET0", "wind": "Wind (2 m)",
             "maxmin": "max / min", "mean": "mean", "max": "max", "gust": "gust", "grass": "grass", "hum": "Mean / min humidity", "sun": "Sunshine hours",
             "spray_ok": "Good for spraying", "spray_no": "Windy for spraying", "src": "Station data – San Vicente, San Alberto, Córdoba", "gen": "Generated"}}
SPRAY_KMH = 15  # same rule as the wind alert: 2 m mean wind above 15 km/h = windy
def num(v, lang, n=1):
    if v is None: return "–"
    s = f"{v:.{n}f}"; return s.replace(".", ",") if lang == "es" else s
def build(S, langs, test=False):
    d = dt.date.fromisoformat(S["date"]); dd = d.strftime("%d/%m"); p, q = langs[0], (langs[1] if len(langs) > 1 else None)
    subj = (f"San Vicente – resumen del día {dd}" if p == "es" else f"San Vicente – daily summary {dd}")
    if test: subj = "[TEST] " + subj
    gen = dt.datetime.now(TZ).strftime("%d/%m/%Y %H:%M") + " (ART)"
    T = lambda k: TX[p][k]; T2 = lambda k: TX[q][k] if q else ""
    icon = "🌧" if (S["rain"] or 0) >= 1 else "☀" if (S["sun"] or 0) >= 8 else "⛅"
    head = lambda l: f'{icon} {num(S["tmax"], l, 0)} °C · {num(S["rain"], l)} mm {TX[l]["rainw"]} · ET0 {num(S["et0"], l)} mm'
    dline = lambda l: f'{TX[l]["day"]} – {DOWN[l][d.weekday()]} {d.strftime("%d/%m/%Y")}'
    windy = S["wmean"] is not None and S["wmean"] > SPRAY_KMH
    spray = lambda l: ("✖ " + TX[l]["spray_no"]) if windy else ("✔ " + TX[l]["spray_ok"])
    notes = []  # (kind, {lang: text})
    for n, v in S["notes"]: notes.append((n, {l: L[l][n].format(num(v, l, 0 if n in ("heat", "chill") else 1)) for l in langs}))
    cards = [("🌡", "temp", f'{num(S["tmax"], p)}° / {num(S["tmin"], p)}°', "maxmin"),
             ("💧", "rain", f'{num(S["rain"], p)} mm', None),
             ("🌱", "et0", f'{num(S["et0"], p)} mm', "grass"),
             ("💨", "wind", f'{num(S["wmean"], p)} km/h', None)]
    def sub(k, l):
        if k == "wind": return f'{TX[l]["mean"]} · {TX[l]["max"]} {num(S["wmax"], l)} · {TX[l]["gust"]} {num(S["gust"], l)} km/h'
        return TX[l]["grass"] if k == "et0" else ""
    F = "font-family:Arial,Helvetica,sans-serif;"
    def card(ic, k, val, _):
        return (f'<td class="c" width="50%" valign="top" style="padding:6px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f6f9;border-radius:10px">'
                f'<tr><td style="padding:14px 14px 12px;{F}"><div style="font-size:22px;line-height:26px">{ic}</div>'
                f'<div style="font-size:26px;line-height:32px;font-weight:bold;color:#1c3d55">{html.escape(val)}</div>'
                f'<div style="font-size:13px;color:#333;font-weight:bold">{T(k)}{(" <span style=\"color:#8a97a3;font-weight:normal\">/ " + T2(k) + "</span>") if q else ""}</div>'
                f'<div style="font-size:12px;color:#5c6b77;margin-top:2px">{html.escape(sub(k, p))}</div></td></tr></table></td>')
    grid = "".join(f'<tr>{card(*cards[i])}{card(*cards[i+1])}</tr>' for i in (0, 2))
    det = [(f'{T("hum")}' + (f' <span style="color:#8a97a3">/ {T2("hum")}</span>' if q else ""), f'{num(S["rhmean"], p, 0)} % / {num(S["rhmin"], p, 0)} %'),
           (f'{T("sun")}' + (f' <span style="color:#8a97a3">/ {T2("sun")}</span>' if q else ""), f'{num(S["sun"], p)} h')]
    dt_html = "".join(f'<tr><td style="padding:9px 4px;border-bottom:1px solid #e3e8ec;{F}font-size:14px;color:#333">{a}</td><td align="right" style="padding:9px 4px;border-bottom:1px solid #e3e8ec;{F}font-size:14px;font-weight:bold;color:#1c3d55;white-space:nowrap">{b}</td></tr>' for a, b in det)
    sc, sb = ("#b3261e", "#fdecea") if windy else ("#1e7b34", "#e7f5ea")
    spray_h = f'<div style="{F}font-size:15px;font-weight:bold;color:{sc}">{spray(p)}</div>' + (f'<div style="{F}font-size:13px;color:{sc}">{spray(q)}</div>' if q else "")
    note_h = ""
    for n, txt in notes:
        bg, bd = ("#fff4e5", "#e8833a") if n == "heat" else ("#e8f1fb", "#4a86c5") if n == "chill" else ("#fff8d6", "#d9b100")
        note_h += (f'<tr><td style="padding:6px 6px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="background:{bg};border-left:4px solid {bd};border-radius:6px;padding:10px 12px;{F}font-size:14px;color:#3a3a3a">'
                   + html.escape(txt[p]) + (f'<br><span style="font-size:12px;color:#6b6b6b">{html.escape(txt[q])}</span>' if q else "") + '</td></tr></table></td></tr>')
    hh = (f'<!DOCTYPE html><html lang="{p}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{html.escape(subj)}</title>'
          '<style>@media only screen and (max-width:480px){.c{display:block!important;width:100%!important;box-sizing:border-box}}</style></head>'
          '<body style="margin:0;padding:0;background:#e9eef2"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e9eef2"><tr><td align="center" style="padding:16px 8px">'
          '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden">'
          f'<tr><td style="background:#24577a;padding:20px 22px;{F}color:#ffffff"><div style="font-size:13px;letter-spacing:1px;text-transform:uppercase;opacity:.85">San Vicente Weather</div>'
          f'<div style="font-size:20px;font-weight:bold;margin-top:4px">{html.escape(dline(p))}</div>' + (f'<div style="font-size:13px;opacity:.8">{html.escape(dline(q))}</div>' if q else "")
          + f'<div style="font-size:17px;margin-top:10px">{html.escape(head(p))}</div></td></tr>'
          f'<tr><td style="padding:12px 10px 4px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{grid}</table></td></tr>'
          f'<tr><td style="padding:6px 16px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{dt_html}</table></td></tr>'
          f'<tr><td style="padding:12px 16px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="background:{sb};border-radius:8px;padding:11px 14px">{spray_h}</td></tr></table></td></tr>'
          + (f'<tr><td style="padding:6px 10px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{note_h}</table></td></tr>' if note_h else "")
          + f'<tr><td style="padding:18px 18px 20px;{F}font-size:11px;color:#8a97a3;line-height:16px">{html.escape(TX[p]["src"])}' + (f' · {html.escape(TX[q]["src"])}' if q else "")
          + f'<br>{TX[p]["gen"]}{(" / " + TX[q]["gen"]) if q else ""}: {gen} · <a href="https://prabath9052.github.io/san-vicente-weather/" style="color:#24577a">San Vicente Weather</a>'
          + f'<br>{html.escape(" / ".join(L[l]["unsub"] for l in langs))}</td></tr></table></td></tr></table></body></html>')
    def txt_sec(l):
        t = TX[l]; out = [dline(l), head(l), "",
              f'{t["temp"]}: {num(S["tmax"], l)} / {num(S["tmin"], l)} °C', f'{t["rain"]}: {num(S["rain"], l)} mm',
              f'{t["et0"]} ({t["grass"]}): {num(S["et0"], l)} mm', f'{t["wind"]}: {sub("wind", l).replace(t["mean"], num(S["wmean"], l) + " km/h " + t["mean"], 1)}',
              f'{t["hum"]}: {num(S["rhmean"], l, 0)} % / {num(S["rhmin"], l, 0)} %', f'{t["sun"]}: {num(S["sun"], l)} h', spray(l)]
        return "\n".join(out + [x[l] for _, x in notes])
    txt = "San Vicente Weather\n\n" + ("\n\n" + "-" * 40 + "\n\n").join(txt_sec(l) for l in langs) + f'\n\n{TX[p]["src"]} · {gen}\n' + " / ".join(L[l]["unsub"] for l in langs) + "\n"
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
        subj, txt, hh = build(S, ["en", "es"]); print(subj); print(txt)
        if E("PREVIEW_HTML"): open(E("PREVIEW_HTML"), "w", encoding="utf-8").write(hh); print("preview written:", E("PREVIEW_HTML"))
        return
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
        subj, txt, hh = build(S, [lg] if lg else ["en", "es"], TEST)
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
