#!/usr/bin/env python3
"""Offline test of daily_summary.py with a mock SMTP server and mock recipients (no network, no secrets).
Checks: STARTTLS + login + one mail per recipient, invalid email dropped, summarySentDate written, lastSentDate untouched,
second run skips. Usage: python3 scripts/test_daily_summary.py"""
import os, sys, json, tempfile, smtplib, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
st = tempfile.mktemp(suffix=".json"); json.dump({"lastSentDate": "KEEP"}, open(st, "w"))
os.environ.update(STATE_FILE=st, SMTP_HOST="mock", SMTP_PORT="587", SMTP_USER="u", SMTP_PASS="p", MAIL_FROM="bot@example.com", MOCK_TO="a@example.com,not-an-email,b@example.org")
SENT = []
class FakeSMTP:
    def __init__(self, *a, **k): self.tls = False
    def starttls(self, **k): self.tls = True
    def login(self, u, p): assert self.tls
    def send_message(self, m): SENT.append(m)
    def quit(self): pass
smtplib.SMTP = FakeSMTP
import daily_summary as d
H = d.hourly_station(["data/station-series.json"]); days = sorted({k[:10] for k in H})
day = next(x for x in reversed(days) if sum(k.startswith(x) for k in H) >= 22)
class FakeDT(dt.datetime):
    @classmethod
    def now(cls, tz=None): return dt.datetime.combine(dt.date.fromisoformat(day) + dt.timedelta(days=1), dt.time(8, 5), d.TZ)
d.dt = type("m", (), {"datetime": FakeDT, "date": dt.date, "timedelta": dt.timedelta, "time": dt.time})
d.main(); s = json.load(open(st))
assert len(SENT) == 2, SENT; assert s["summarySentDate"] == day and s["lastSentDate"] == "KEEP", s
assert "resumen del día" in SENT[0]["Subject"] and SENT[0].get_body("html") is not None
d.main(); assert len(SENT) == 2  # once per day
print("OK:", day, SENT[0]["Subject"], "->", len(SENT), "mails;", s)
