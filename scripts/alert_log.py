"""Append a REAL sent alert to data/alerts-log.json (newest first, last 100). No personal data."""
import json, os
LOG = "data/alerts-log.json"
def log_alert(time, typ, value, text, text_es, gust=None, observed=None):
    try: a = json.load(open(LOG))
    except Exception: a = []
    a.insert(0, {"time": time, "type": typ, "value": value, "gust": gust, "observed": observed, "text": text, "textEs": text_es})
    json.dump(a[:100], open(LOG, "w"), indent=1, ensure_ascii=False)
