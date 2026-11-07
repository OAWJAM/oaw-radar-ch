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


## Czechia — ČHMÚ's open data (7 Nov, the owner: «CZ ja»)

The Czech Hydrometeorological Institute publishes its radar products on an **open-data portal** (`opendata.chmi.cz/meteorology/weather/radar/`): the
composites over the Czech Republic every five minutes as ODIM HDF5 (v2.4) and PNG — MAX_Z, PseudoCAPPI 2 km, a merged 1-h precipitation, echo tops and
two extrapolation forecasts — on a 1 km Mercator grid (EPSG:3857, about 11.3–19.6 E, 48.0–51.5 N). The source `cz` reads the **MAX_Z composite**
(`composite/maxz/hdf5/T_PABV23_C_OKPR_<YYYYMMDDhhmmss>.hdf`, listed on the folder's index page), reflectivity through Marshall–Palmer like DMI's, onto
0.01° latitude/longitude; no key. The licence line says CC BY 4.0 with the attribution ČHMÚ: the ČHMÚ open-data page carries the CC BY 4.0 mark, while the data server and the
radar description PDFs name none — `grids.py --probe --only cz` prints what the portal's front page says (nothing, as of 7 Nov), the raw value
histogram of the newest file and how much of the portal's own PNG of that minute is painted (a dry picture is a dry picture: on 7 Nov 09:50Z the
field had 0.7 % of its cells above undetect, all under 5 dBZ, and the PNG 2.4 % painted).

## Retired sources (7 Nov)

The Nordic composite carries the Swedish and Finnish radars, so the single sources `se` (SMHI) and `fi` (FMI) are retired — marked `retired` in their tables, skipped by the runs, their folders removed from the site (`RETIRED` in wms.py). Their code stays; `--only se` or `--only fi` runs one on purpose.


## The world mosaic — Rainbow.ai (7 Nov, ready to switch on)

`rainbow.py` fetches Rainbow.ai's radar mosaic as Web-Mercator XYZ tiles, stitches them, decodes the reflectivity (`color=dbz_u8`: the red channel carries dBZ + 32, bit 7 marks snow, a transparent pixel means no radar), paints the mosaic in this site's own scale and publishes it like every other source — as `rb-<area>/` with `frames.json` (`crs: EPSG:3857`, which the boards lay on their Mercator chart in one piece), `latest.png`, `status.json`, `coverage.json` (the reach's edge from the transparent pixels) and `usage.json` (the month's tile count). It lies at the bottom of the layer; the national pictures sit on it.

**Off until the secret exists.** Settings → Secrets and variables → Actions → New repository secret → `RAINBOW_API_KEY` (the token from developer.rainbow.ai → Profile). Without it the step logs «no key — skipped» and the run stays green. The key travels in a header, never in a URL or a log.

**Steered by repository variables** (the same page, the *Variables* tab; a missing one takes the default):

| Variable | Default | Meaning |
|---|---|---|
| `RAINBOW_LAYER` | `radars` | `radars` = the radar mosaic (zoom 0–7) · `precip-global` = radar + satellite + nowcast, the oceans too (zoom 0–12) · `precip` |
| `RAINBOW_AREAS` | `world:3` | a comma list of `<name>:<zoom>[:<x0>-<x1>:<y0>-<y1>]`; presets `world:3` (8 × 8 = 64 tiles, the whole world, ~20 km a pixel) and `europe:5` (8 × 6 = 48 tiles, 22.5 W – 67.5 E · 21.9 – 66.5 N, ~5 km a pixel); `world:3,europe:5` takes both |
| `RAINBOW_CADENCE_MIN` | `30` | a new picture only when the newest one is at least this old (the snapshot's clock) — the lever on the cost |
| `RAINBOW_MONTHLY_TILES` | `100000` | the month's tile budget; reached, the runs skip until the next month and `status.json` says so — never a surprise bill |
| `RAINBOW_COLOR` | `dbz_u8` | the palette asked of the API; `dbz_u8` is decoded into this site's scale, a number (0–9) keeps Rainbow's own picture as it is |

**What it costs** (Rainbow's tile price as read on 6 Nov: 30 000 tiles a month free, then 0.0002 $ a tile; the price list is theirs to change): one snapshot call plus the area's tiles per fetch. `world:3` at 30 min = 48 × 65 × 30 ≈ 94 000 tiles a month ≈ **13 $**; at 15 min ≈ 31 $; at 60 min ≈ 3 $. `europe:5` at 30 min ≈ 8 $, at 15 min ≈ 22 $. Both areas at 30 min ≈ 27 $. GitHub runs the workflow about once an hour under load (the schedule says ten minutes), so the real cadence — and the bill — is often below the variable; `usage.json` keeps the count.

**First keyed run**: Actions → *probe* → Run workflow logs what each layer answers (the snapshot, a tile's size and mode, whether `dbz_u8` is served for the chosen layer). Should a layer not serve `dbz_u8`, set `RAINBOW_COLOR` to a palette number — the picture then comes as Rainbow paints it, the coverage edge still from its transparent pixels.
