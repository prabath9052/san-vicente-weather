"""Bot Firestore helpers for daily_summary.py (bot sign-in + alertNominees list)."""
import os
from fs_sync import KEY, FS, req
def token():
    return req(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
               {"email": os.environ["FB_BOT_EMAIL"], "password": os.environ["FB_BOT_PASSWORD"], "returnSecureToken": True})["idToken"]
def list_nominees(tok):
    return req(FS + "/alertNominees?pageSize=300", tok=tok).get("documents", [])
