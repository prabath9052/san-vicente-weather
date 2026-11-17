#!/usr/bin/env python3
"""English versions of SMN alert texts (headline / description / instruction), for the app banner.
SMN writes its CAP alerts in Spanish from a small set of standard templates, so the translation is rule based:
  1. description: each sentence is matched against sentence templates ('El área será afectada por X.',
     'Las mismas podrán estar acompañadas por X.', 'Se prevén valores de precipitación acumulada entre A y B mm ...')
     and the slot X is translated phrase by phrase from a glossary of SMN hazard phrases joined by ',', 'y', 'o', 'con';
     an ALL-CAPS short-term text ('TORMENTAS SEVERAS CON LLUVIAS INTENSAS, RAFAGAS Y CAIDA DE GRANIZO') is one phrase list;
  2. instruction: numbered items ('1- ...') / sentences looked up in a dictionary of SMN's standard advice;
  3. headline: 'AVISO NARANJA POR <phrase list>' or an event name.
A sentence nothing matches is never guessed: it goes to an optional machine-translation fallback (MyMemory public API,
no key; off unless SMN_MT=1; results cached in the state file) and, if that is off or fails, the field has no English
version (the app then shows SMN's Spanish text and says so). Each result reports how it was made: 'dict' (all rules),
'mt' / 'mixed' (machine translation used) or None (not translated)."""
import json, os, re, unicodedata, urllib.parse, urllib.request

def n(s):
    """normalised key: no accents, lower case, single spaces, no trailing punctuation"""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip().rstrip(".;:").strip()

DIRS = {"norte": "north", "sur": "south", "este": "east", "oeste": "west", "noreste": "north-east", "noroeste": "north-west",
        "sudeste": "south-east", "sureste": "south-east", "sudoeste": "south-west", "suroeste": "south-west"}
D_RE = "(" + "|".join(sorted(DIRS, key=len, reverse=True)) + ")"
# hazard phrases (normalised Spanish regex -> English); longest first is applied automatically
PH = [
 (r"tormentas severas", "severe storms"), (r"tormentas fuertes", "strong storms"), (r"tormentas aisladas", "isolated storms"),
 (r"tormentas fuertes aisladas", "isolated strong storms"), (r"tormentas", "storms"), (r"lluvias intensas", "heavy rain"),
 (r"lluvias persistentes", "persistent rain"), (r"lluvias fuertes", "heavy rain"), (r"lluvias", "rain"), (r"lluvia", "rain"),
 (r"algunas localmente severas", "some locally severe"), (r"algunas localmente fuertes", "some locally strong"),
 (r"algunas puntualmente severas", "some locally severe"), (r"algunas fuertes", "some strong"), (r"algunas severas", "some severe"),
 (r"puntualmente severas", "locally severe"), (r"localmente severas", "locally severe"), (r"localmente fuertes", "locally strong"),
 (r"(?:abundantes|fuertes|intensas) precipitaciones en cortos periodos", "heavy rainfall in short periods"),
 (r"(?:abundante|fuerte|intensa) precipitacion en cortos periodos", "heavy rainfall in short periods"),
 (r"precipitaciones intensas en cortos periodos", "heavy rainfall in short periods"),
 (r"(?:actividad electrica frecuente|frecuente actividad electrica)", "frequent lightning"),
 (r"(?:intensa actividad electrica|actividad electrica intensa)", "intense lightning"), (r"actividad electrica", "lightning"),
 (r"granizo de diversos tamanos", "hail of various sizes"), (r"granizo de gran tamano", "large hail"),
 (r"(?:granizo ocasional|ocasional granizo|ocasional caida de granizo|caida ocasional de granizo)", "occasional hail"),
 (r"(?:caida de granizo|granizo)", "hail"),
 (r"rafagas intensas que pueden localmente superar los (\d+) ?km/h", r"strong gusts that may locally exceed \1 km/h"),
 (r"rafagas que pueden localmente superar los (\d+) ?km/h", r"gusts that may locally exceed \1 km/h"),
 (r"rafagas que pueden superar los (\d+) ?km/h", r"gusts that may exceed \1 km/h"),
 (r"rafagas (?:de|superiores a) (\d+) ?km/h", r"gusts of \1 km/h"),
 (r"(?:rafagas intensas|fuertes rafagas|rafagas fuertes)", "strong gusts"), (r"rafagas", "gusts"),
 (r"vientos? del sector " + D_RE + r" con velocidades entre (\d+) y (\d+) ?km/h", lambda m: f"{DIRS[m[1]]} winds of {m[2]}–{m[3]} km/h"),
 (r"vientos? del sector " + D_RE, lambda m: f"{DIRS[m[1]]} winds"),
 (r"viento zonda con velocidades entre (\d+) y (\d+) ?km/h", r"Zonda wind of \1–\2 km/h"), (r"viento zonda", "Zonda wind"),
 (r"vientos? con velocidades entre (\d+) y (\d+) ?km/h", r"winds of \1–\2 km/h"), (r"vientos? fuertes", "strong winds"), (r"vientos?", "wind"),
 (r"nevadas de variada intensidad", "snowfall of varying intensity"), (r"nevadas", "snowfall"), (r"nieve", "snow"),
 (r"por momentos de variada intensidad", "at times of varying intensity"), (r"de variada intensidad", "of varying intensity"),
 (r"bancos de niebla que reduciran la visibilidad a menos de (\d+) ?(?:metros|m)", r"fog banks reducing visibility to below \1 m"),
 (r"bancos de niebla", "fog banks"), (r"niebla", "fog"), (r"polvo en suspension", "blowing dust"),
 (r"temperaturas extremas", "extreme temperatures"), (r"temperaturas muy elevadas", "very high temperatures"),
]
PH = [(re.compile(r"(?<![a-z])" + p + r"(?![a-z])"), e) for p, e in PH]
CONN = [(", ", ", "), (" y ", " and "), (" e ", " and "), (" o ", " or "), (" u ", " or "), (" con ", " with "), (" acompanadas de ", " with ")]

def phrases(s):
    """translate a normalised list of hazard phrases; None if any word is left over"""
    out, i = [], 0; s = s.strip().rstrip(",")
    while i < len(s):
        best = max(((m, e) for rx, e in PH for m in [rx.match(s, i)] if m), key=lambda x: x[0].end(), default=None)
        if best:   # longest phrase wins
            m, e = best; out.append(e(m) if callable(e) else m.expand(e)); i = m.end()
        else:
            for es, en in CONN:
                if s.startswith(es, i): out.append(en); i += len(es); break
            else: return None
    return "".join(out)

def cap1(s): return s[:1].upper() + s[1:] if s else s
LV = {"amarillo": "Yellow", "naranja": "Orange", "rojo": "Red"}
SENT = [
 (r"el area (?:sera|se vera|podria ser) afectada por (.+)", lambda m, p: f"The area will be affected by {p(m[1])}."),
 (r"(?:las mismas|los mismos|estas|estos|ademas,? estas) (?:podran|pueden|podrian) (?:estar )?(?:acompanad[ao]s|ir acompanad[ao]s) (?:por|de) (.+)", lambda m, p: f"These may be accompanied by {p(m[1])}."),
 (r"se (?:preven|esperan|prevee?n) valores de precipitacion acumulada entre (\d+) y (\d+) ?mm(,? que (?:pueden|podrian) ser superados (?:de forma|en forma) (?:local|puntual))?",
  lambda m, p: f"Accumulated rainfall of {m[1]}–{m[2]} mm is expected" + (", locally more." if m[3] else ".")),
 (r"se (?:preven|esperan) valores de precipitacion acumulada de (\d+) ?mm(,? que (?:pueden|podrian) ser superados (?:de forma|en forma) (?:local|puntual))?",
  lambda m, p: f"Accumulated rainfall of about {m[1]} mm is expected" + (", locally more." if m[2] else ".")),
 (r"se (?:preve|espera) un marcado (?:ascenso|aumento) de la temperatura y (?:un )?descenso de la humedad", lambda m, p: "A marked rise in temperature and drop in humidity is expected."),
 (r"aviso (amarillo|naranja|rojo) por (.+)", lambda m, p: f"{LV[m[1]]} warning: {p(m[2])}"),
 (r"efecto leve a moderado en la salud", lambda m, p: "Mild to moderate health effects."),
 (r"efecto moderado a alto en la salud", lambda m, p: "Moderate to high health effects."),
 (r"efecto alto a extremo en la salud", lambda m, p: "High to extreme health effects."),
 (r"pueden ser peligrosas, sobre todo para los grupos de riesgo,? como bebes y ninos pequenos, mayores de 65 anos y personas con enfermedades cronicas",
  lambda m, p: "They can be dangerous, especially for risk groups such as babies and young children, people over 65 and people with chronic diseases."),
 (r"pueden ser muy peligrosas,? (?:aun|incluso) para (?:las )?personas saludables", lambda m, p: "They can be very dangerous, even for healthy people."),
]
SENT = [(re.compile(p + r"$"), f) for p, f in SENT]

INS = {n(k): v for k, v in {
 "Seguí las instrucciones de las autoridades locales": "Follow the instructions of the local authorities.",
 "La calma es fundamental en estas situaciones": "Staying calm is essential in these situations.",
 "Salí solo si es necesario": "Only go out if necessary.",
 "Evitá salir": "Avoid going out.",
 "Permanecé en construcciones cerradas como casas, escuelas, edificios públicos": "Stay inside enclosed buildings such as houses, schools or public buildings.",
 "Permanecé en construcciones cerradas como casas, escuelas o edificios públicos": "Stay inside enclosed buildings such as houses, schools or public buildings.",
 "Desconectá los electrodomésticos y cortá el suministro eléctrico si ingresa agua": "Unplug appliances and switch off the power if water gets in.",
 "Cerrá y alejate de puertas y ventanas": "Close doors and windows and keep away from them.",
 "Alejate de zonas inundables y no ingreses en calles anegadas": "Keep away from flood-prone areas and do not enter flooded streets.",
 "Retirá o asegurá objetos que puedan ser volados por el viento": "Remove or secure objects that could be blown away by the wind.",
 "Retirá o asegurá objetos que puedan ser arrastrados por el viento": "Remove or secure objects that could be blown away by the wind.",
 "Prepará un kit de emergencia con agua potable, alimentos, botiquín de primeros auxilios, radio a pilas y linterna": "Prepare an emergency kit with drinking water, food, a first-aid kit, a battery radio and a torch.",
 "Cargá los celulares con anticipación": "Charge your phones in advance.",
 "Agendá los números locales de Protección Civil, Bomberos y Policía": "Keep the local Civil Protection, fire brigade and police numbers at hand.",
 "No saques la basura y limpiá desagües y sumideros": "Do not put the rubbish out, and clear drains and gutters.",
 "Si estás al aire libre, buscá refugio inmediato en un edificio, casa o vehículo cerrado": "If you are outdoors, take shelter immediately in a building, house or closed vehicle.",
 "Evitá circular por calles inundadas o afectadas": "Avoid driving or walking through flooded or affected streets.",
 "No te refugies debajo de marquesinas, carteles publicitarios, árboles o postes": "Do not shelter under awnings, billboards, trees or poles.",
 "Mantenete alejado de artefactos eléctricos y evitá el uso de teléfonos con cable": "Keep away from electrical appliances and avoid using corded phones.",
 "Para minimizar el riesgo de ser alcanzado por un rayo, no permanezcas en playas, ríos, lagunas o piletas": "To reduce the risk of being struck by lightning, do not stay on beaches, rivers, lakes or swimming pools.",
 "Evitá actividades al aire libre": "Avoid outdoor activities.",
 "Asegurá puertas y ventanas": "Secure doors and windows.",
 "Hidratate constantemente": "Keep hydrated.", "Tomá agua constantemente": "Drink water regularly.",
 "Evitá la exposición al sol en horas centrales del día": "Avoid sun exposure in the middle of the day.",
}.items()}

SPLIT = re.compile(r"(?<=[.!?])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])")
def sentences(t): return [s for s in SPLIT.split((t or "").strip()) if s.strip()]
def items(t):
    """'1- A. B 2- C' -> [['A.', 'B'], ['C']] (numbered items, each split into sentences)"""
    parts = [p for p in re.split(r"(?:^|\s)\d{1,2}\s*-\s+", " " + (t or "").strip()) if p.strip()]
    return [sentences(p) for p in parts]

def one_desc(s):
    k = n(s)
    for rx, f in SENT:
        m = rx.match(k)
        if m:
            bad = []
            def p(x):
                r = phrases(x)
                if r is None: bad.append(x)
                return r or ""
            r = f(m, p)
            return None if bad else r
    r = phrases(k)          # ALL-CAPS short-term phrase list ('TORMENTAS SEVERAS CON LLUVIAS INTENSAS, ...')
    return cap1(r) + ("." if s.rstrip().endswith(".") else "") if r else None

def one_ins(s): return INS.get(n(s))

def mt(s, cache):
    """machine-translation fallback (MyMemory, no key). Off unless SMN_MT=1. Cached by normalised text."""
    k = n(s)
    if k in cache: return cache[k]
    if os.environ.get("SMN_MT") != "1" or len(s) > 480: return None
    try:
        u = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": s, "langpair": "es|en"})
        d = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "san-vicente-weather"}), timeout=15).read())
        t = (d.get("responseData") or {}).get("translatedText")
        if d.get("responseStatus") in (200, "200") and t and "MYMEMORY" not in t.upper():
            cache[k] = t; return t
    except Exception as e: print("smn: translation fallback failed", type(e).__name__)
    return None

def translate(text, kind, cache=None):
    """-> (english or None, 'dict' | 'mt' | 'mixed' | None)"""
    if not text or not text.strip(): return None, None
    cache = {} if cache is None else cache
    groups = items(text) if kind == "instruction" else [[s] for s in sentences(text)]
    out, how = [], set()
    for g in groups:
        tr = []
        for s in g:
            r = one_ins(s) if kind == "instruction" else one_desc(s)
            if r: how.add("dict")
            else:
                r = mt(s, cache)
                if not r: return None, None
                how.add("mt")
            tr.append(r)
        out.append(" ".join(tr))
    if kind == "instruction" and re.search(r"\d\s*-\s", text):
        return "\n".join(f"{i + 1}. {x}" for i, x in enumerate(out)), ("mixed" if len(how) > 1 else how.pop())
    return " ".join(out), ("mixed" if len(how) > 1 else how.pop())

def english(a, cache=None, ev_en=None):
    """{'headline','description','instruction','how'} English versions of an alert's texts (None where not translated)"""
    r, how = {}, set()
    for f in ("headline", "description", "instruction"):
        t = a.get(f)
        if f == "headline" and t and ev_en and n(t) == n(a.get("event")): e, h = ev_en(t), "dict"
        else: e, h = translate(t, f if f == "instruction" else "description", cache)
        r[f] = e
        if t and t.strip(): how.add(h)
    r["how"] = None if (not how or None in how) and not any(r[f] for f in ("headline", "description", "instruction")) else \
        ("dict" if how == {"dict"} else "mt" if how == {"mt"} else "partial" if None in how else "mixed")
    return r
