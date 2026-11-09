#!/usr/bin/env python3
"""Forecast skill from the stored 1-day-ahead history (data/verification.json days[].fm / days[].o).
Item 3 - model weights: per variable, weight_m ~ 1/MAE_m over the last WINDOW verified days, with a floor
(no model below FLOOR x the equal weight) and a fallback to equal weights when fewer than MIN_DAYS days.
Item 2 - bias correction: mean error (forecast - station) of the *weighted* blend over the same days, for tmax/tmin,
applied as  corrected = blend - bias, capped at +/-CAP degC; no correction when fewer than MIN_DAYS days.
Rain gets weights only (an additive rain correction would invent rain on dry days)."""
import statistics as stt
MODELS = ["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "gem_seamless", "best_match"]
WEIGHT_VARS = ("tmax", "tmin", "rain"); BIAS_VARS = ("tmax", "tmin")
WINDOW, MIN_DAYS, CAP, FLOOR = 30, 10, 3.0, 0.5
BLEND = {"tmax": "mean", "tmin": "mean", "rain": "median"}  # rain is skewed (one model at 20 mm), so a weighted median

def _num(x): return isinstance(x, (int, float)) and x == x

def weights(days, var, models=MODELS, min_days=MIN_DAYS, floor=FLOOR):
    """days: [{'fm':{model:{var:..}}, 'o':{var:..}}]. Returns ({model: weight}, n_days, {model: mae})."""
    mae, n = {}, 0
    for m in models:
        e = [abs(d["fm"][m][var] - d["o"][var]) for d in days
             if _num(d.get("o", {}).get(var)) and _num(d.get("fm", {}).get(m, {}).get(var))]
        if len(e) >= min_days: mae[m] = stt.mean(e); n = max(n, len(e))
    if len(mae) < 2: return {m: round(1 / len(models), 3) for m in models}, n, mae  # fallback: equal weights
    raw = {m: 1 / max(mae.get(m, max(mae.values())), 0.1) for m in models}  # a model without history = worst known MAE
    s = sum(raw.values()); w = {m: v / s for m, v in raw.items()}
    lo = floor / len(models)                                          # floor, then renormalise (repeat until stable)
    for _ in range(10):
        fixed = {m for m in w if w[m] <= lo + 1e-12}
        free = sum(w[m] for m in w if m not in fixed); rest = 1 - lo * len(fixed)
        w = {m: lo if m in fixed else w[m] * rest / free for m in w}
        if all(v >= lo - 1e-9 for v in w.values()): break
    return {m: round(v, 3) for m, v in w.items()}, n, {m: round(v, 2) for m, v in mae.items()}

def blend(fm, w, var, models=MODELS, how=None):
    """Accuracy-weighted blend of the models available that day (weights renormalised); None if none.
    how: 'mean' or 'median' (default per variable from BLEND). Weighted median = the value where the cumulative weight first reaches half."""
    p = [(fm[m][var], w.get(m, 0)) for m in models if m in fm and _num(fm[m].get(var))]
    s = sum(x[1] for x in p)
    if not p or s <= 0: return None
    if (how or BLEND.get(var, "median")) == "mean": return sum(v * k for v, k in p) / s
    p.sort(); c = 0.0
    for i, (v, k) in enumerate(p):
        c += k
        if abs(c - s / 2) < 1e-9 and i + 1 < len(p): return (v + p[i + 1][0]) / 2  # exact half: average the two (= plain median when equal)
        if c > s / 2: return v
    return p[-1][0]

def median_blend(fm, var, models=MODELS):
    v = [fm[m][var] for m in models if m in fm and _num(fm[m].get(var))]
    return stt.median(v) if v else None

def compute(days, before=None, window=WINDOW, min_days=MIN_DAYS, cap=CAP):
    """Skill from verified days strictly before `before` (ISO date) - last `window` of them."""
    H = sorted((d for d in days if d.get("fm") and d.get("o") and (before is None or d["d"] < before)), key=lambda d: d["d"])[-window:]
    out = {"n": len(H), "window": window, "min_days": min_days, "cap": cap, "from": H[0]["d"] if H else None,
           "to": H[-1]["d"] if H else None, "weights": {}, "mae": {}, "bias": {}, "corr": {}}
    for v in WEIGHT_VARS:
        w, n, mae = weights(H, v, min_days=min_days); out["weights"][v] = w; out["mae"][v] = mae
    for v in BIAS_VARS:
        e = [b - d["o"][v] for d in H if _num(d["o"].get(v)) and _num(b := blend(d["fm"], out["weights"][v], v))]
        if len(e) >= min_days:
            bias = stt.mean(e); out["bias"][v] = round(bias, 2); out["corr"][v] = round(max(-cap, min(cap, -bias)), 2)
        else: out["bias"][v] = None; out["corr"][v] = 0.0
    return out

def forecast(fm, sk, var):
    """New blend for one day: accuracy-weighted mean + bias correction (tmax/tmin)."""
    b = blend(fm, sk["weights"].get(var, {m: 1 for m in MODELS}), var)
    if b is None: return None
    if var == "rain": return max(0.0, b)
    return b + (sk["corr"].get(var) or 0.0)

def day_blend(days, day, src):
    """New-blend 1-day-ahead forecast for a verified day, using only skill the app could have had when that forecast
    was issued (verified days before the day before). src: 'live' (stored when verified) or 'backtest' (computed later)."""
    import datetime as _dt
    if not day.get("fm"): return None
    cut = (_dt.date.fromisoformat(day["d"]) - _dt.timedelta(days=1)).isoformat()
    sk = compute(days, before=cut); out = {"n": sk["n"], "src": src}
    for v in WEIGHT_VARS:
        x = forecast(day["fm"], sk, v); out[v] = round(x, 1) if x is not None else None
    return out
