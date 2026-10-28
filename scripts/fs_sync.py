#!/usr/bin/env python3
"""Mirror private station data to Firestore 'private/{doc}' (readable by signed-in users only, written by the bot).
Docs: station (data/station.json), stationSeries (data/station-series.json), alertsLog (data/alerts-log.json),
windState / humidityState (alert state files). Each doc: {json: <string>, updated: <timestamp>}.
Only changed docs are written (hash cache in $RUNNER_TEMP or /tmp). Never fails the workflow.
alertsLog is merged with the stored copy and written with an updateTime precondition (A-11), so the
station and ET0 jobs (separate concurrency groups) can't overwrite each other's new alert entries."""
import json, os, sys, hashlib, urllib.request, urllib.error, datetime as dt
KEY = "AIzaSyD2kxZAxQw2Ux46PVBSI6HsuD0-Pc1ko0I"
FS = "https://firestore.googleapis.com/v1/projects/san-vicente-weather/databases/(default)/documents"
DOCS = {"station": "data/station.json", "stationSeries": "data/station-series.json", "alertsLog": "data/alerts-log.json",
        "windState": "data/wind-alert-state.json", "humidityState": "data/humidity-alert-state.json",
        "verification": "data/verification.json", "etoState": "data/eto-state.json"}
TMP = os.environ.get("RUNNER_TEMP", "/tmp"); HC = os.path.join(TMP, "fs_sync_hashes.json")
def req(url, data=None, tok=None, method=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method)
    return json.load(urllib.request.urlopen(r, timeout=30))
def _ts(e):
    try: return dt.datetime.fromisoformat(str(e.get("time")).replace("Z", "+00:00")).timestamp()
    except Exception: return 0
def merge_log(local, remote):
    """Union of both alert lists (exact-duplicate entries once), newest first, last 100."""
    seen, out = set(), []
    for e in local + remote:
        k = json.dumps(e, sort_keys=True, ensure_ascii=False)
        if k not in seen: seen.add(k); out.append(e)
    out.sort(key=_ts, reverse=True)  # stable: equal/unparseable times keep their order
    return out[:100]
def write_log(raw, tok, now, tries=4):
    """alertsLog is appended by both the station job and the ET0 job (separate concurrency groups, A-11).
    Compare-and-set: read the stored log, merge in its entries, write only if the doc is unchanged since
    that read (Firestore currentDocument.updateTime precondition); retry on conflict. Updates data/alerts-log.json."""
    local = json.loads(raw)
    for i in range(tries):
        try: d = req(f"{FS}/private/alertsLog", tok=tok); pre = "&currentDocument.updateTime=" + d["updateTime"]
        except urllib.error.HTTPError as e:
            if e.code != 404: raise
            d, pre = {}, "&currentDocument.exists=false"
        try: remote = json.loads(d.get("fields", {}).get("json", {}).get("stringValue") or "[]")
        except Exception: remote = []
        merged = merge_log(local, remote if isinstance(remote, list) else [])
        out = json.dumps(merged, indent=1, ensure_ascii=False)
        body = {"fields": {"json": {"stringValue": out}, "updated": {"timestampValue": now}}}
        try:
            req(f"{FS}/private/alertsLog?updateMask.fieldPaths=json&updateMask.fieldPaths=updated{pre}", body, tok=tok, method="PATCH")
        except urllib.error.HTTPError as e:
            if e.code in (400, 409, 412) and i < tries - 1: print("fs_sync: alertsLog changed meanwhile, retrying"); continue
            raise
        if out != raw: open(DOCS["alertsLog"], "w", encoding="utf-8").write(out)
        return out, hashlib.sha1(out.encode()).hexdigest()
def main():
    if not os.environ.get("FB_BOT_PASSWORD"): print("fs_sync: no bot secrets; skipping"); return
    hc = json.load(open(HC)) if os.path.exists(HC) else {}
    todo = {}
    for name, path in DOCS.items():
        if not os.path.exists(path): continue
        if os.environ.get("ONLY_DOCS") and name not in os.environ["ONLY_DOCS"].split(","): continue
        raw = open(path, encoding="utf-8").read(); h = hashlib.sha1(raw.encode()).hexdigest()
        if hc.get(name) != h: todo[name] = (raw, h)
    if not todo: print("fs_sync: nothing changed"); return
    tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
              {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"); ok = []
    for name, (raw, h) in todo.items():
        try:
            if name == "alertsLog": raw, h = write_log(raw, tok, now)
            else:
                body = {"fields": {"json": {"stringValue": raw}, "updated": {"timestampValue": now}}}
                req(f"{FS}/private/{name}?updateMask.fieldPaths=json&updateMask.fieldPaths=updated", body, tok=tok, method="PATCH")
            hc[name] = h; ok.append(name)
        except urllib.error.HTTPError as e:
            print(f"fs_sync: {name} write failed HTTP {e.code}" + (" (rules not published yet?)" if e.code == 403 else ""))
    json.dump(hc, open(HC, "w")); print("fs_sync: wrote", ", ".join(ok) or "none")
if __name__ == "__main__":
  try: main()
  except Exception as e: print("fs_sync failed:", type(e).__name__, getattr(e, "code", ""))
