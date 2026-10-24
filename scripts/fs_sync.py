#!/usr/bin/env python3
"""Mirror private station data to Firestore 'private/{doc}' (readable by signed-in users only, written by the bot).
Docs: station (data/station.json), stationSeries (data/station-series.json), alertsLog (data/alerts-log.json),
windState / humidityState (alert state files). Each doc: {json: <string>, updated: <timestamp>}.
Only changed docs are written (hash cache in $RUNNER_TEMP or /tmp). Never fails the workflow."""
import json, os, sys, hashlib, urllib.request, urllib.error, datetime as dt
KEY = "AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I"
FS = "https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
DOCS = {"station": "data/station.json", "stationSeries": "data/station-series.json", "alertsLog": "data/alerts-log.json",
        "windState": "data/wind-alert-state.json", "humidityState": "data/humidity-alert-state.json"}
TMP = os.environ.get("RUNNER_TEMP", "/tmp"); HC = os.path.join(TMP, "fs_sync_hashes.json")
def req(url, data=None, tok=None, method=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method)
    return json.load(urllib.request.urlopen(r, timeout=30))
def main():
    if not os.environ.get("FB_BOT_PASSWORD"): print("fs_sync: no bot secrets; skipping"); return
    hc = json.load(open(HC)) if os.path.exists(HC) else {}
    todo = {}
    for name, path in DOCS.items():
        if not os.path.exists(path): continue
        raw = open(path, encoding="utf-8").read(); h = hashlib.sha1(raw.encode()).hexdigest()
        if hc.get(name) != h: todo[name] = (raw, h)
    if not todo: print("fs_sync: nothing changed"); return
    tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
              {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); ok = []
    for name, (raw, h) in todo.items():
        body = {"fields": {"json": {"stringValue": raw}, "updated": {"timestampValue": now}}}
        try:
            req(f"{FS}/private/{name}?updateMask.fieldPaths=json&updateMask.fieldPaths=updated", body, tok=tok, method="PATCH")
            hc[name] = h; ok.append(name)
        except urllib.error.HTTPError as e:
            print(f"fs_sync: {name} write failed HTTP {e.code}" + (" (rules not published yet?)" if e.code == 403 else ""))
    json.dump(hc, open(HC, "w")); print("fs_sync: wrote", ", ".join(ok) or "none")
try: main()
except Exception as e: print("fs_sync failed:", type(e).__name__, getattr(e, "code", ""))
