#!/usr/bin/env python3
"""Fetch an hourly + daily precipitation grid (median of 5 Open-Meteo models) and write data/precip.json.
Paced to respect Open-Meteo free-tier limits (~600 location-calls/min)."""
import json, time, statistics, urllib.request, urllib.error, datetime, os
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
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            wait = 65 if e.code == 429 else 15
            print(f'HTTP {e.code}, retry in {wait}s'); time.sleep(wait)
        except Exception as e:
            print('error', e); time.sleep(15)
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
    print(f'{i + len(b)}/{len(pts)} points, {time.time() - t0:.0f}s')
    if i + BATCH < len(pts): time.sleep(PAUSE)

ref = next(r for r in res if r)
htime, dtime = ref['hourly']['time'], ref['daily']['time']
q = lambda v: 0 if v is None else int(round(v * 10))  # 0.1 mm units
hourly = [[q(med([r['hourly'].get(f'precipitation_{m}', [None]*len(htime))[k] for m in MODELS])) for r in res] for k in range(len(htime))]
daily = [[q(med([r['daily'].get(f'precipitation_sum_{m}', [None]*len(dtime))[k] for m in MODELS])) for r in res] for k in range(len(dtime))]
out = {'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'tz': TZ,
       'models': MODELS, 'unit': '0.1 mm', 'bounds': {'s': S, 'n': N, 'w': W, 'e': E}, 'step': STEP,
       'nx': len(lons), 'ny': len(lats), 'order': 'row-major from south-west (lat rows, lon cols)',
       'start': htime[0], 'days': dtime, 'hourly': hourly, 'daily': daily}
os.makedirs('data', exist_ok=True)
with open('data/precip.json', 'w') as f: json.dump(out, f, separators=(',', ':'))
print('wrote data/precip.json', os.path.getsize('data/precip.json'), 'bytes')
