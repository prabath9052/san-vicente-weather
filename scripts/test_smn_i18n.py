#!/usr/bin/env python3
"""Tests for smn_i18n.py (English versions of SMN alert texts). Offline: machine translation is never called."""
import os, sys, re, glob
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.environ.pop("SMN_MT", None)
import smn_i18n as T, smn_alerts as S
def ok(c, m):
    if not c: raise AssertionError(m)
SPANISH = re.compile(r"\b(el|la|las|los|por|con|del|que|será|podrán|lluvias?|tormentas?|ráfagas|granizo|viento|salir|evitá)\b", re.I)
# 1 every saved CAP sample is fully translated by the rules, numbering kept, no Spanish words left
for f in sorted(glob.glob(os.path.join(HERE, "testdata", "smn", "*.xml"))):
    if f.endswith("feed.xml"): continue
    a = S.parse_cap(open(f, "rb").read(), "x/" + os.path.basename(f)); e = T.english(a, {}, S.ev_en)
    ok(e["how"] == "dict", (f, e))
    for k in ("headline", "description", "instruction"):
        ok(e[k] and not SPANISH.search(e[k]), (f, k, e[k]))
    if re.search(r"\d\s*-\s", a["instruction"]):
        ok(e["instruction"].count("\n") + 1 == len(re.findall(r"(?:^|\s)\d{1,2}\s*-\s", a["instruction"])), ("numbering", e["instruction"]))
# 2 the yellow storm alert reads as expected
a = S.parse_cap(open(os.path.join(HERE, "testdata/smn/alert_yellow_storms_cordillera_20.xml"), "rb").read(), "x/y")
e = T.english(a, {}, S.ev_en)
ok(e["description"] == "The area will be affected by rain and isolated storms, some locally strong. These may be accompanied by heavy rainfall in short periods, occasional hail, frequent lightning and gusts. Accumulated rainfall of 15–40 mm is expected, locally more.", e["description"])
ok(e["instruction"].startswith("1. Avoid going out.\n2. Do not put the rubbish out"), e["instruction"]); ok(e["headline"] == "Storms", e)
# 3 short-term headline / ALL-CAPS phrase list
ok(T.translate("AVISO AMARILLO POR TORMENTAS FUERTES CON LLUVIAS INTENSAS, RAFAGAS Y OCASIONAL CAIDA DE GRANIZO", "description")[0] == "Yellow warning: strong storms with heavy rain, gusts and occasional hail", "short headline")
# 4 other SMN templates (wind, rain, Zonda, fog) and the longest phrase wins
ok(T.translate("El área será afectada por vientos del sector sur con velocidades entre 40 y 55 km/h y ráfagas que pueden superar los 75 km/h.", "description")[0] == "The area will be affected by south winds of 40–55 km/h and gusts that may exceed 75 km/h.", "wind")
ok(T.translate("El área será afectada por lluvias persistentes, por momentos de variada intensidad. Se esperan valores de precipitación acumulada entre 60 y 90 mm, que pueden ser superados en forma puntual.", "description")[0].endswith("Accumulated rainfall of 60–90 mm is expected, locally more."), "rain")
ok("hail of various sizes" in T.translate("Las mismas podrán estar acompañadas por granizo de diversos tamaños.", "description")[0], "longest match")
ok(T.translate("El área será afectada por viento Zonda con velocidades entre 35 y 50 km/h. Se prevé un marcado ascenso de la temperatura y descenso de la humedad.", "description")[0].startswith("The area will be affected by Zonda wind of 35–50 km/h."), "zonda")
# 5 never guess: an unknown sentence or a leftover word -> no English (app shows Spanish); a known + unknown mix -> None too
ok(T.translate("Se registrarán heladas fuertes en zonas bajas.", "description") == (None, None), "unknown sentence")
ok(T.translate("El área será afectada por tormentas y heladas.", "description") == (None, None), "leftover word")
ok(T.translate("Evitá salir. Hacé algo raro.", "instruction") == (None, None), "unknown advice")
a2 = dict(a, description="Se registrarán heladas fuertes en zonas bajas."); e2 = T.english(a2, {}, S.ev_en)
ok(e2["description"] is None and e2["instruction"] and e2["how"] == "partial", e2)
# 6 machine-translation fallback: only from the cache / when SMN_MT=1 (here: cached result), flagged as 'mixed'
c = {T.n("Se registrarán heladas fuertes en zonas bajas."): "Hard frosts in low-lying areas."}
ok(T.translate("El área será afectada por tormentas. Se registrarán heladas fuertes en zonas bajas.", "description", c) == ("The area will be affected by storms. Hard frosts in low-lying areas.", "mixed"), "mt cache")
ok(T.mt("Otra frase.", {}) is None, "mt off without SMN_MT=1")
ok(T.translate("", "description") == (None, None) and T.english({"headline": None, "description": "", "instruction": None})["how"] is None, "empty")
print("smn_i18n: all checks passed")
