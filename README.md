# Kite-Wind

Windvorhersage für Kitespots aus den DWD-Modellen ICON-D2 (2,2 km, 48 h) und ICON-EU (7 km, 120 h),
Daten von [opendata.dwd.de](https://opendata.dwd.de). Darstellung angelehnt an Windguru.

## So funktioniert es

- `fetch_icon.py` lädt die neuesten Modellläufe und schreibt pro Spot und Modell eine JSON-Datei nach `web/data/`.
- `web/index.html` ist eine statische Seite, die diese Dateien anzeigt.
- `.github/workflows/forecast.yml` führt das Skript alle 3 Stunden aus und veröffentlicht `web/` auf GitHub Pages.
  Die Daten werden nicht ins Repo committet.

## Spots ändern

`spots.json` bearbeiten (id, Name, Breite, Länge) und committen. Der nächste Lauf übernimmt sie.

## Einrichtung

Einmalig unter *Settings → Pages → Build and deployment → Source* „GitHub Actions“ wählen.
Danach unter *Actions → Vorhersage aktualisieren → Run workflow* den ersten Lauf starten.

## Lokal testen

```
pip install -r requirements.txt
python fetch_icon.py
cd web && python -m http.server 8000   # http://localhost:8000
```
