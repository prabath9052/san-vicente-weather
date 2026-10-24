#!/usr/bin/env python3
"""Restore private working files (station data, alert history, alert state) from Firestore 'private/*'
into data/ at the start of each Action run. These files are git-ignored and never committed.
Exits 1 if Firestore can't be read, so alerts never run with lost state (would re-send)."""
import json, os, sys, hashlib, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_sync import KEY, FS, DOCS, HC, req
tok = req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
          {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
os.makedirs("data", exist_ok=True); hc = {}
for name, path in DOCS.items():
    try: d = req(f"{FS}/private/{name}", tok=tok)
    except urllib.error.HTTPError as e:
        if e.code == 404: print("fs_pull:", name, "not in Firestore yet"); continue
        print("fs_pull:", name, "HTTP", e.code); sys.exit(1)
    raw = d.get("fields", {}).get("json", {}).get("stringValue")
    if raw is None: continue
    open(path, "w", encoding="utf-8").write(raw); hc[name] = hashlib.sha1(raw.encode()).hexdigest()
json.dump(hc, open(HC, "w")); print("fs_pull: restored", ", ".join(hc))
