#!/usr/bin/env python3
"""Tests for the self-chaining station workflow: station_chain.decide/main (fake GitHub API, never dispatches for real),
heartbeat.py, and the station.yml polling loop run locally with stub scripts and shortened times. No network, no secrets."""
import os, sys, json, re, shutil, subprocess, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); ROOT = os.path.dirname(HERE)
import station_chain as C
def ok(c, m):
    if not c: raise AssertionError(m)
B = {"GITHUB_REF": "refs/heads/main", "DEFAULT_BRANCH": "main", "GITHUB_RUN_ID": "100"}
now = time.time()
# 1 decide(): the guards
ok(C.decide(B, [], now) == (True, "dispatch next run"), "plain handoff")
ok(not C.decide(dict(B, FORCE_THRESHOLD="5"), [], now)[0], "test runs never chain")
for v in ("off", "OFF", "0", "false"): ok(not C.decide(dict(B, STATION_CHAIN=v), [], now)[0], "kill switch " + v)
ok(C.decide(dict(B, STATION_CHAIN="on"), [], now)[0], "switch on")
ok(not C.decide(dict(B, GITHUB_REF="refs/heads/station-chain"), [], now)[0], "only from the default branch")
ok(not C.decide(B, [{"id": 7, "status": "pending"}], now)[0] and "already waiting" in C.decide(B, [{"id": 7, "status": "queued"}], now)[1], "a waiting run exists")
ok(C.decide(B, [{"id": 100, "status": "queued"}], now)[0], "own run ignored")
ok(C.decide(B, [{"id": 8, "status": "in_progress"}], now)[0], "a running run is not a waiting one (concurrency keeps the new one waiting)")
ok(not C.decide(dict(B, MODE="final", RUN_START=str(now - 5 * 60)), [], now)[0], "crash at start: no chain")
ok(C.decide(dict(B, MODE="final", RUN_START=str(now - 90 * 60)), [], now)[0], "stopped after 90 min: chain")
# 2 main() with a fake API: lists all waiting states, dispatches once on the default branch with chain=1; never prints the token
calls = []
def fake(method, path, body=None):
    calls.append((method, path, body)); return {"workflow_runs": FAKE_RUNS} if method == "GET" else {}
C.api = fake; FAKE_RUNS = []
os.environ.update(B, GITHUB_REPOSITORY="o/r", GITHUB_TOKEN="SECRET-TOKEN-XYZ", MODE="handoff"); os.environ.pop("DRY_RUN", None)
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf): C.main()
gets = [c for c in calls if c[0] == "GET"]; posts = [c for c in calls if c[0] == "POST"]
ok({re.search(r"status=(\w+)", g[1])[1] for g in gets} == C.WAITING, gets)
ok(len(posts) == 1 and posts[0][1] == "/repos/o/r/actions/workflows/station.yml/dispatches" and posts[0][2] == {"ref": "main", "inputs": {"chain": "1"}}, posts)
ok("SECRET-TOKEN" not in buf.getvalue(), "token printed")
calls.clear(); FAKE_RUNS = [{"id": 55, "status": "pending"}]
with contextlib.redirect_stdout(buf): C.main()
ok(not [c for c in calls if c[0] == "POST"], "no dispatch when a run is waiting")
calls.clear(); FAKE_RUNS = []; os.environ["STATION_CHAIN"] = "off"
with contextlib.redirect_stdout(buf): C.main()
ok(not calls, "kill switch: no API calls at all"); os.environ.pop("STATION_CHAIN")
# 3 the workflow loop, run for real with stub scripts and minutes shortened to seconds
wf = open(os.path.join(ROOT, ".github/workflows/station.yml")).read()
ok("actions: write" in wf and "timeout-minutes: 350" in wf and "330*60" in wf and "- cron:" in wf, "workflow settings")
script = wf.split("        run: |\n", 1)[1].split("\n      # the run stopped early")[0]
script = "\n".join(l[10:] for l in script.splitlines())
script = script.replace("330*60", "8").replace("12*60", "4").replace("sleep 150", "sleep 1").replace("-ge 600 ]", "-ge 3 ]")
t = tempfile.mkdtemp(); os.makedirs(t + "/scripts"); os.makedirs(t + "/data"); log = t + "/calls.log"
for n in ("fs_pull", "fetch_station", "wind_alert", "humidity_alert", "verify", "fs_sync", "station_health", "smn_alerts", "station_chain"):
    open(f"{t}/scripts/{n}.py", "w").write(f"import os,sys\nopen({log!r},'a').write('{n} '+os.environ.get('MODE','')+' '+os.environ.get('ONLY_DOCS','')+'\\n')\n" +
        ("import json,time;json.dump({'observed':str(int(time.time()//3)),'lastConnection':'x'},open('data/station.json','w'))\n" if n == "fetch_station" else ""))
shutil.copy(os.path.join(HERE, "heartbeat.py"), t + "/scripts/heartbeat.py")
json.dump({"observed": "0"}, open(t + "/data/station.json", "w"))   # fs_pull restores it in the real run
genv = t + "/github_env"; open(genv, "w").close()
env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "FORCE_THRESHOLD", "MODE")}; env.update(GITHUB_ENV=genv, GITHUB_RUN_ID="42")
r = subprocess.run(["bash", "-e", "-c", script], cwd=t, env=env, capture_output=True, text=True, timeout=60)
ok(r.returncode == 0, (r.returncode, r.stdout[-800:], r.stderr[-800:]))
L = open(log).read().splitlines(); G = open(genv).read()
ok(sum(1 for l in L if l.startswith("station_chain handoff")) == 1, ("one handoff", L))
ok("CHAIN_HANDED=1" in G and "RUN_START=" in G, G)
ok(sum(1 for l in L if l.startswith("fetch_station")) >= 4 and sum(1 for l in L if l.startswith("station_health")) == sum(1 for l in L if l.startswith("fetch_station")), "health every pass")
ok(any("heartbeat" in l for l in L if l.startswith("fs_sync")), "heartbeat saved")
ok(any(l.startswith("smn_alerts") for l in L) and any(l.startswith("wind_alert") for l in L), "alerts keep running")
hb = json.load(open(t + "/data/heartbeat.json")); ok(hb["fetchOk"] is True and hb["run"] == "42" and hb["lastConnection"] == "x" and hb["at"], hb)
# test run (FORCE_THRESHOLD): single pass, no chain, no heartbeat sync
open(log, "w").close(); open(genv, "w").close()
r = subprocess.run(["bash", "-e", "-c", script], cwd=t, env=dict(env, FORCE_THRESHOLD="1"), capture_output=True, text=True, timeout=60)
L = open(log).read().splitlines()
ok(r.returncode == 0 and not any(l.startswith("station_chain") for l in L) and not any("heartbeat" in l for l in L) and sum(1 for l in L if l.startswith("fetch_station")) == 1, L)
# 4 heartbeat after a failed fetch keeps the last good time
os.chdir(t); subprocess.run([sys.executable, "scripts/heartbeat.py", "0"], check=True)
hb2 = json.load(open("data/heartbeat.json")); ok(hb2["fetchOk"] is False and hb2["lastOk"] == hb["lastOk"], hb2)
shutil.rmtree(t)
print("station_chain: all checks passed")
