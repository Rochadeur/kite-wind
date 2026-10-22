"""Lädt ICON-D2 / ICON-EU Punktvorhersagen von opendata.dwd.de und schreibt JSON pro Spot.

Aufruf:  python fetch_icon.py [--model icon-d2|icon-eu|all] [--out web/data]
Benötigt: pip install eccodes requests
"""
import argparse
import bz2
import datetime as dt
import json
import math
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

import eccodes
import requests

BASE = "https://opendata.dwd.de/weather/nwp"
PARAMS = ["u_10m", "v_10m", "vmax_10m", "t_2m", "clct", "tot_prec"]
MS_TO_KN = 1.943844

MODELS = {
    # D2: Läufe alle 3 h, 48 h Vorhersage, stündlich
    "icon-d2": {
        "runs": range(0, 24, 3),
        "steps": lambda run: range(0, 49),
        "url": lambda run, step, p: (
            f"{BASE}/icon-d2/grib/{run:%H}/{p}/"
            f"icon-d2_germany_regular-lat-lon_single-level_{run:%Y%m%d%H}_{step:03d}_2d_{p}.grib2.bz2"),
        "delay_h": 2,
    },
    # EU: 00/06/12/18 bis 120 h (bis 78 h stündlich, danach 3-stündlich)
    "icon-eu": {
        "runs": range(0, 24, 6),
        "steps": lambda run: list(range(0, 79)) + list(range(81, 121, 3)),
        "url": lambda run, step, p: (
            f"{BASE}/icon-eu/grib/{run:%H}/{p}/"
            f"icon-eu_europe_regular-lat-lon_single-level_{run:%Y%m%d%H}_{step:03d}_{p.upper()}.grib2.bz2"),
        "delay_h": 4,
    },
}

session = requests.Session()


def latest_run(model):
    """Neuester Lauf, dessen letzte u_10m-Datei schon auf dem Server liegt."""
    cfg = MODELS[model]
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    for back in range(0, 30):
        run = now - dt.timedelta(hours=back)
        if run.hour not in cfg["runs"]:
            continue
        last = list(cfg["steps"](run))[-1]
        if session.head(cfg["url"](run, last, "u_10m"), timeout=20).status_code == 200:
            return run
    raise RuntimeError(f"Kein vollständiger Lauf für {model} gefunden")


def grid_indices(msg, spots):
    """Index des nächsten Gitterpunkts je Spot (reguläres Lat/Lon-Gitter)."""
    la1 = eccodes.codes_get(msg, "latitudeOfFirstGridPointInDegrees")
    lo1 = eccodes.codes_get(msg, "longitudeOfFirstGridPointInDegrees")
    di = eccodes.codes_get(msg, "iDirectionIncrementInDegrees")
    dj = eccodes.codes_get(msg, "jDirectionIncrementInDegrees")
    ni = eccodes.codes_get(msg, "Ni")
    nj = eccodes.codes_get(msg, "Nj")
    sign = 1 if eccodes.codes_get(msg, "jScansPositively") else -1
    if lo1 > 180:
        lo1 -= 360
    out = {}
    for s in spots:
        i = round((s["lon"] - lo1) / di)
        j = round(sign * (s["lat"] - la1) / dj)
        if not (0 <= i < ni and 0 <= j < nj):
            raise ValueError(f"{s['name']} liegt außerhalb des Modellgebiets")
        out[s["id"]] = j * ni + i
    return out


def fetch_field(url, spots, idx_cache):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    msg = eccodes.codes_new_from_message(bz2.decompress(r.content))
    try:
        if not idx_cache:
            idx_cache.update(grid_indices(msg, spots))
        vals = eccodes.codes_get_values(msg)
        return {sid: float(vals[k]) for sid, k in idx_cache.items()}
    finally:
        eccodes.codes_release(msg)


def run_model(model, spots, out_dir):
    cfg = MODELS[model]
    run = latest_run(model)
    steps = list(cfg["steps"](run))
    print(f"{model}: Lauf {run:%Y-%m-%d %HZ}, {len(steps)} Zeitschritte", file=sys.stderr)

    idx_cache = {}
    # Ersten Schritt seriell, damit der Gitterindex gesetzt ist
    fetch_field(cfg["url"](run, 0, "u_10m"), spots, idx_cache)
    jobs = [(p, st) for p in PARAMS for st in steps]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda job: fetch_field(cfg["url"](run, job[1], job[0]), spots, idx_cache), jobs))
    data = {(p, st): v for (p, st), v in zip(jobs, results)}

    for s in spots:
        sid = s["id"]
        rows = []
        prev_prec = 0.0
        for st in steps:
            u, v = data[("u_10m", st)][sid], data[("v_10m", st)][sid]
            prec = data[("tot_prec", st)][sid]
            rows.append({
                "t": (run + dt.timedelta(hours=st)).strftime("%Y-%m-%dT%H:%MZ"),
                "wind": round(math.hypot(u, v) * MS_TO_KN, 1),
                "gust": round(data[("vmax_10m", st)][sid] * MS_TO_KN, 1),
                # meteorologische Richtung: woher der Wind kommt
                "dir": round((math.degrees(math.atan2(-u, -v)) + 360) % 360),
                "temp": round(data[("t_2m", st)][sid] - 273.15, 1),
                "cloud": round(data[("clct", st)][sid]),
                "rain": round(max(prec - prev_prec, 0.0), 1),
            })
            prev_prec = prec
        path = out_dir / f"{sid}.{model}.json"
        path.write_text(json.dumps({
            "spot": s, "model": model, "run": run.strftime("%Y-%m-%dT%H:%MZ"),
            "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "rows": rows,
        }, ensure_ascii=False))
        print(f"  -> {path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="all", choices=["all", *MODELS])
    ap.add_argument("--spots", default=pathlib.Path(__file__).with_name("spots.json"), type=pathlib.Path)
    ap.add_argument("--out", default=pathlib.Path(__file__).with_name("web") / "data", type=pathlib.Path)
    a = ap.parse_args()
    spots = json.loads(a.spots.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "spots.json").write_text(json.dumps(spots, ensure_ascii=False))
    for m in (MODELS if a.model == "all" else [a.model]):
        run_model(m, spots, a.out)


if __name__ == "__main__":
    main()
