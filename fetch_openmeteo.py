"""Lädt DMI HARMONIE und ECMWF IFS Punktvorhersagen über Open-Meteo und schreibt JSON pro Spot.

Gleiches Ausgabeformat wie fetch_icon.py: web/data/<spot>.<modell>.json
Daten: Open-Meteo.com (CC BY 4.0), freie API nur für nicht-kommerzielle Nutzung.
Ein Abruf pro Modell für alle Spots zusammen, also nur wenige Aufrufe pro Stunde.

Aufruf:  python fetch_openmeteo.py [--model dmi|ecmwf|all] [--out web/data]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

import requests

API = "https://api.open-meteo.com/v1/forecast"
META = "https://api.open-meteo.com/data/{}/static/meta.json"
HOURLY = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "temperature_2m", "cloud_cover", "precipitation"]

MODELS = {
    # DMI HARMONIE AROME Europe: 2 km, alle 3 h neu, ca. 60 h. Open-Meteo hängt danach ECMWF an,
    # deshalb auf die eigene Reichweite begrenzen.
    "dmi": {"om": "dmi_harmonie_arome_europe", "hours": 60},
    # ECMWF IFS HRES 9 km, alle 6 h neu, offen seit Okt. 2025
    "ecmwf": {"om": "ecmwf_ifs", "hours": 7 * 24},
}

session = requests.Session()
iso = lambda t: t.strftime("%Y-%m-%dT%H:%MZ")


def model_run(om):
    """Startzeit des neuesten Laufs laut Open-Meteo-Metadaten (None, falls nicht verfügbar)."""
    try:
        r = session.get(META.format(om), timeout=20)
        r.raise_for_status()
        return iso(dt.datetime.fromtimestamp(r.json()["last_run_initialisation_time"], dt.timezone.utc))
    except Exception as e:
        print(f"  Metadaten für {om} nicht lesbar: {e}", file=sys.stderr)
        return None


def run_model(model, spots, out_dir):
    cfg = MODELS[model]
    r = session.get(API, timeout=60, params={
        "latitude": ",".join(str(s["lat"]) for s in spots),
        "longitude": ",".join(str(s["lon"]) for s in spots),
        "hourly": ",".join(HOURLY),
        "models": cfg["om"],
        "wind_speed_unit": "kn",
        "timezone": "GMT",
        "timeformat": "unixtime",
        "forecast_hours": cfg["hours"],
    })
    r.raise_for_status()
    res = r.json()
    if isinstance(res, dict):
        res = [res]
    run = model_run(cfg["om"])
    fetched = iso(dt.datetime.now(dt.timezone.utc))
    print(f"{model} ({cfg['om']}): Lauf {run}", file=sys.stderr)

    for s, d in zip(spots, res):
        h = d["hourly"]
        rows = []
        for i, t in enumerate(h["time"]):
            wind, gust = h["wind_speed_10m"][i], h["wind_gusts_10m"][i]
            if wind is None or h["wind_direction_10m"][i] is None:
                continue
            rows.append({
                "t": iso(dt.datetime.fromtimestamp(t, dt.timezone.utc)),
                "wind": round(wind, 1),
                "gust": round(gust if gust is not None else wind, 1),
                "dir": round(h["wind_direction_10m"][i]),
                "temp": round(h["temperature_2m"][i] or 0, 1),
                "cloud": round(h["cloud_cover"][i] or 0),
                "rain": round(h["precipitation"][i] or 0, 1),
            })
        path = out_dir / f"{s['id']}.{model}.json"
        path.write_text(json.dumps({
            "spot": s, "model": model, "run": run or rows[0]["t"], "fetched": fetched, "rows": rows,
        }, ensure_ascii=False))
        print(f"  -> {path} ({len(rows)} Stunden)", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="all", choices=["all", *MODELS])
    ap.add_argument("--spots", default=pathlib.Path(__file__).with_name("spots.json"), type=pathlib.Path)
    ap.add_argument("--out", default=pathlib.Path(__file__).with_name("web") / "data", type=pathlib.Path)
    a = ap.parse_args()
    spots = json.loads(a.spots.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for m in (MODELS if a.model == "all" else [a.model]):
        # Ein Modell darf ausfallen, ohne die anderen (und die ICON-Daten) zu blockieren
        try:
            run_model(m, spots, a.out)
        except Exception as e:
            print(f"{m}: Abruf fehlgeschlagen: {e}", file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
