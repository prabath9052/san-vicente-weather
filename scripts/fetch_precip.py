#!/usr/bin/env python3
"""Fetch an hourly + daily precipitation grid (median of 5 Open-Meteo models) and write data/precip.json.
Paced to respect Open-Meteo free-tier limits (~600 location-calls/min)."""
import json, time, statistics, datetime, os
import requests
SESSION = requests.Session()  # keep-alive: avoids a new TLS handshake per batch
S, N, W, E, STEP = -42.0, -20.0, -75.5, -50.0, 0.75
MODELS = ['ecmwf_ifs025', 'gfs_seamless', 'icon_seamless', 'gem_seamless', 'best_match']
TZ = 'America/Argentina/Cordoba'
lats = [round(S + i * STEP, 3) for i in range(int((N - S) / STEP + 1e-9) + 1)]
lons = [round(W + i * STEP, 3) for i in range(int((E - W) / STEP + 1e-9) + 1)]
pts = [(la, lo) for la in lats for lo in lons]
BATCH, PAUSE = 50, 7.0

def med(v):
    v = [x for x in v if isinstance(x, (int, float))]
    return statistics.median(v) if v else None

def get(url):
    for a in range(6):
        try:
            r = SESSION.get(url, timeout=(15, 120))
            if r.status_code == 200:
                return r.json()
            wait = 65 if r.status_code == 429 else 15
            print(f'HTTP {r.status_code}, retry in {wait}s', flush=True)
        except Exception as e:
            wait = 5
            print('error', e, flush=True)
        time.sleep(wait)
    raise SystemExit('giving up')

res = [None] * len(pts)
t0 = time.time()
for i in range(0, len(pts), BATCH):
    b = pts[i:i + BATCH]
    url = ('https://api.open-meteo.com/v1/forecast?latitude=' + ','.join(str(p[0]) for p in b) +
           '&longitude=' + ','.join(str(p[1]) for p in b) + '&hourly=precipitation&daily=precipitation_sum&models=' +
           ','.join(MODELS) + '&timezone=' + TZ.replace('/', '%2F') + '&forecast_days=7')
    d = get(url); d = d if isinstance(d, list) else [d]
    for j, r in enumerate(d): res[i + j] = r
    print(f'{i + len(b)}/{len(pts)} points, {time.time() - t0:.0f}s', flush=True)
    if i + BATCH < len(pts): time.sleep(PAUSE)

ref = next(r for r in res if r)
htime, dtime = ref['hourly']['time'], ref['daily']['time']
q = lambda v: 0 if v is None else int(round(v * 100))  # 0.01 mm units
H = len(htime)
def series(r, m, key, n): return r[key].get(f'precipitation{"_sum" if key=="daily" else ""}_{m}', [None]*n)
daily_med = [[med([series(r, m, 'daily', len(dtime))[k] for m in MODELS]) or 0 for r in res] for k in range(len(dtime))]
# hourly: multi-model MEAN timing, scaled so each day's 24 h add up to that day's median daily total
hourly_f = [[0.0]*len(res) for _ in range(H)]
for p_, r in enumerate(res):
    for k, day in enumerate(dtime):
        idx = [i for i, t in enumerate(htime) if t.startswith(day)]
        mean = []
        for i in idx:
            v = [series(r, m, 'hourly', H)[i] for m in MODELS]; v = [x for x in v if isinstance(x, (int, float))]
            mean.append(sum(v)/len(v) if v else 0.0)
        tot = sum(mean); f = daily_med[k][p_]/tot if tot > 0 else 0.0
        for i, mv in zip(idx, mean): hourly_f[i][p_] = mv*f
hourly = [[q(v) for v in row] for row in hourly_f]
daily = [[q(v) for v in row] for row in daily_med]
out = {'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'tz': TZ,
       'models': MODELS, 'unit': '0.01 mm', 'scale': 100, 'method': 'daily = model median; hourly = model-mean timing scaled to the daily median', 'bounds': {'s': S, 'n': N, 'w': W, 'e': E}, 'step': STEP,
       'nx': len(lons), 'ny': len(lats), 'order': 'row-major from south-west (lat rows, lon cols)',
       'start': htime[0], 'days': dtime, 'hourly': hourly, 'daily': daily}
os.makedirs('data', exist_ok=True)
with open('data/precip.json', 'w') as f: json.dump(out, f, separators=(',', ':'))
print('wrote data/precip.json', os.path.getsize('data/precip.json'), 'bytes')
