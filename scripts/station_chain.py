#!/usr/bin/env python3
"""Self-chaining for the station workflow (GitHub's cron now starts it only every ~7-10 h, see station.yml).
Near the end of a long polling run this starts the NEXT run with workflow_dispatch, using the run's own built-in
GITHUB_TOKEN (needs `permissions: actions: write`; dispatch is the one event a GITHUB_TOKEN may trigger).
The concurrency group 'station' keeps that next run waiting ('pending') until this one ends, so polling hands over
with a ~1-2 min gap. Guards against duplicate / overlapping chains:
  * concurrency group: at most one running + one waiting run ever exists (a newer waiting run replaces an older one);
  * before dispatching, the API is asked for station runs that are queued/pending/waiting/requested (other than this
    one): if there is one (e.g. a late cron run), nothing is dispatched - that run is the next link;
  * only from the default branch, never from test runs (FORCE_THRESHOLD) and not when the repository variable
    STATION_CHAIN is 'off' (kill switch, no code change needed);
  * the 'after failure' call (MODE=final) only dispatches if this run polled for >= MIN_AGE_MIN minutes, so a run that
    crashes at start can't create a fast dispatch loop; the cron schedule stays as the backup restarter.
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_REF, DEFAULT_BRANCH, WORKFLOW_FILE (station.yml),
     STATION_CHAIN, FORCE_THRESHOLD, MODE (handoff|final), RUN_START (epoch s), MIN_AGE_MIN (20), DRY_RUN=1.
Never prints the token."""
import json, os, sys, time, urllib.request, urllib.error
WAITING = {"queued", "pending", "waiting", "requested"}

def api(method, path, body=None):
    tok = os.environ.get("GITHUB_TOKEN", "")
    r = urllib.request.Request("https://api.github.com" + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
                                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "san-vicente-weather-chain"})
    with urllib.request.urlopen(r, timeout=30) as f:
        raw = f.read(); return json.loads(raw) if raw else {}

def decide(env, runs, now):
    """-> (dispatch?, reason). Pure: env dict, runs = list of {id, status}, now = epoch seconds."""
    if env.get("FORCE_THRESHOLD"): return False, "test run: no chain"
    if (env.get("STATION_CHAIN") or "").strip().lower() in ("off", "0", "false", "no"): return False, "chain switched off (STATION_CHAIN)"
    br = env.get("DEFAULT_BRANCH") or "main"
    if env.get("GITHUB_REF") != "refs/heads/" + br: return False, f"not the default branch ({env.get('GITHUB_REF')})"
    if env.get("MODE") == "final":
        try: age = (now - float(env.get("RUN_START") or now)) / 60
        except ValueError: age = 0
        if age < float(env.get("MIN_AGE_MIN") or 20): return False, f"run ended after {age:.0f} min: too early to chain (cron backup restarts)"
    me = str(env.get("GITHUB_RUN_ID") or "")
    nxt = [r for r in runs if str(r.get("id")) != me and r.get("status") in WAITING]
    if nxt: return False, f"next run already waiting (#{nxt[0].get('id')}, {nxt[0].get('status')})"
    return True, "dispatch next run"

def main():
    env = dict(os.environ); repo = env.get("GITHUB_REPOSITORY", ""); wf = env.get("WORKFLOW_FILE", "station.yml")
    pre = {k: v for k, v in env.items() if k in ("FORCE_THRESHOLD", "STATION_CHAIN", "GITHUB_REF", "DEFAULT_BRANCH")}
    ok, why = decide(dict(pre, MODE=env.get("MODE"), RUN_START=env.get("RUN_START"), MIN_AGE_MIN=env.get("MIN_AGE_MIN")), [], time.time())
    if not ok: print("chain:", why); return 0
    runs = []
    for st in sorted(WAITING):
        try: runs += api("GET", f"/repos/{repo}/actions/workflows/{wf}/runs?status={st}&per_page=20").get("workflow_runs", [])
        except urllib.error.HTTPError as e: print("chain: could not list runs", e.code); return 0   # unsure -> don't risk a duplicate
    ok, why = decide(dict(env), [{"id": r.get("id"), "status": r.get("status")} for r in runs], time.time())
    if not ok: print("chain:", why); return 0
    if env.get("DRY_RUN") == "1": print("chain: DRY_RUN - would dispatch", wf, "on", env.get("DEFAULT_BRANCH") or "main"); return 0
    for i in range(3):
        try:
            api("POST", f"/repos/{repo}/actions/workflows/{wf}/dispatches", {"ref": env.get("DEFAULT_BRANCH") or "main", "inputs": {"chain": "1"}})
            print("chain: next run dispatched (waits for this run to finish)"); return 0
        except urllib.error.HTTPError as e: print("chain: dispatch failed", e.code); time.sleep(10 * (i + 1))
        except Exception as e: print("chain: dispatch failed", type(e).__name__); time.sleep(10 * (i + 1))
    return 0
if __name__ == "__main__": sys.exit(main())
