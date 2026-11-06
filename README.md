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
python3 convert.py --out site            # the newest pictures from the STAC API (--backfill 12: the missing ones of the last hour)
python3 convert.py --file some.h5 --out site
```

## More radars through the same site

`wms.py` fetches the national radars that publish a keyless WMS — DWD (Germany, dl-de/by-2-0) and FMI (Finland, CC BY 4.0) — as plain latitude/longitude PNGs at the newest picture time the service names, under `de/` and `fi/` with their own `frames.json`, and writes `sources.json` at the root: the list of every source of this site (the Swiss converter's pictures stay at the root). The pictures carry the services' own colour scales.

`grids.py` adds the pictures that come as a georeferenced grid: SMHI's Swedish composite (a GeoTIFF in SWEREF99 TM, CC BY 4.0) under `se/`, and GeoSphere Austria's INCA precipitation analysis (CC BY 4.0) under `at/` — the Austrian radar composite blended with the rain gauges, 1 km, every 15 minutes, read as the lead-0 step of the open INCA nowcast (`start=end=<run time>`; the earlier runs through `forecast_offset`, so each pass backfills the last 90 minutes). Both are reprojected onto latitude/longitude and painted with the Swiss scale. Since 6 Nov (late) the same script reads **MET Norway's Nordic radar composite** under `no/`: the radars of Norway, Sweden and Finland on one 1 km Lambert grid every five minutes, published as NetCDF on MET Norway's THREDDS server (`remotesensing/reflectivity-nordic/latest/`, read through OPeNDAP at every second cell, CC BY 4.0) — data, not the rendered map pictures of api.met.no, so no colour key is needed; the file's `is_nodata` mask says where no radar sees.


## The coverage edge (6 Nov, late)

Beside every `frames.json` that can say where its radars see, a `coverage.json` carries the edge of that reach as rings of `[lon, lat]` (traced at a tenth of a degree from the pictures' no-data mask: MeteoSwiss' 255, SMHI's 255, the Nordic composite's `is_nodata`, the grey reach DWD paints under its rain). The boards draw the rings as thin lines under the pictures, so a dry area inside the line reads as «no rain», one outside as «no radar». A source whose picture carries no such mark (FMI's WMS, the INCA analysis) writes none.


## The keyed radars (6 Nov, late)

KNMI (Netherlands) and Météo-France (France) hand their composites out only with a free key. The keys are **repository secrets** — Settings → Secrets and variables → Actions — named `KNMI_API_KEY` (the Data Platform key) and `METEOFRANCE_APP_ID` (the portal's application id, the Basic value from «Générer Token»); `AEMET_API_KEY` is reserved for Spain. `grids.py` reads them from the environment and skips a source whose secret is missing, so the run stays green without them. Denmark needs none since DMI opened its API; the Nordic composite needs none either.
