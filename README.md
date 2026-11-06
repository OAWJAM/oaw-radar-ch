# oaw-radar-ch

The MeteoSwiss precipitation radar (open government data, CC-BY) as a small transparent PNG in plain latitude/longitude
(EPSG:4326) for a web map — converted every ten minutes by a GitHub Actions workflow and published on GitHub Pages.

* `latest.png` — the newest rain-rate picture (mm/h), transparent where it is dry or unknown
* `latest.json` — its time, bounding box (`west, south, east, north`), size, colour legend and source file
* `frames.json` + `frames/<YYYYMMDDHHMM>.png` — the last twelve pictures for a loop
* `status.json` — the last run

Source: [MeteoSwiss open data](https://opendatadocs.meteoswiss.ch/d-radar-data/d1-precipitation-radar-products) —
STAC collection `ch.meteoschweiz.ogd-radar-precip`, product RZC (rain rate, every 5 minutes, ODIM HDF5, Swiss LV95).
The pictures must carry «Source: MeteoSwiss». Not for operational use — the official products remain the reference.

## Set-up (once)

1. Settings → Pages → **Source: GitHub Actions**.
2. Actions → *radar* → **Run workflow** (the schedule then runs it every ten minutes; GitHub may delay scheduled runs by a few minutes).
3. The site stands at `https://<owner>.github.io/<repo>/`.

## Local try

```
pip install -r requirements.txt
python3 convert.py --out site            # the newest picture from the STAC API
python3 convert.py --file some.h5 --out site
```

## More radars through the same site

`wms.py` fetches the national radars that publish a keyless WMS — DWD (Germany, dl-de/by-2-0) and FMI (Finland, CC BY 4.0) — as plain latitude/longitude PNGs at the newest picture time the service names, under `de/` and `fi/` with their own `frames.json`, and writes `sources.json` at the root: the list of every source of this site (the Swiss converter's pictures stay at the root). The pictures carry the services' own colour scales.
