#!/usr/bin/env python3
"""Update-job heartbeat for the app (Firestore private/heartbeat via fs_sync): when our update job last checked
HOBOlink and what the station looked like then. Lets the app tell 'our update job is late' (heartbeat old) from
'the station itself stopped uploading' (heartbeat fresh, station's last connection old). Readings only, no secrets.
Usage: heartbeat.py <fetch ok 1|0>   (writes data/heartbeat.json)"""
import json, os, sys, datetime as dt
TZ = dt.timezone(dt.timedelta(hours=-3))
try: st = json.load(open("data/station.json"))
except Exception: st = {}
try: old = json.load(open("data/heartbeat.json"))
except Exception: old = {}
ok = (sys.argv[1:] or ["1"])[0] == "1"
hb = {"at": dt.datetime.now(TZ).isoformat(timespec="seconds"), "fetchOk": ok,
      "lastOk": dt.datetime.now(TZ).isoformat(timespec="seconds") if ok else old.get("lastOk"),
      "lastConnection": st.get("lastConnection"), "observed": st.get("observed"),
      "run": os.environ.get("GITHUB_RUN_ID"), "chain": os.environ.get("CHAIN_RUN") == "1"}
json.dump(hb, open("data/heartbeat.json", "w"))
