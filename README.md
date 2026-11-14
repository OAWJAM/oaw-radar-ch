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

## Germany — DWD's RV composite as data, with the RADVOR steps ahead (14 Nov, the owner: «Radar de/at ja»)

Since 14 Nov `grids.py` source `de` (kind `dwd`) reads the **RV composite** from DWD's open-data server — `opendata.dwd.de/weather/radar/composite/rv/`,
one `composite_rv_YYYYMMDD_HHMM.tar` per five-minute run (and `composite_rv_LATEST.tar`), ODIM HDF5 members inside: `_000` the analysis (ACRR,
mm over 5 min, 1 km polar-stereographic grid of 1100 × 1200 km with the projection and corners in the file's `where` group) and `_005` … `_120`
the RADVOR steps. The analysis becomes the picture (mm/h = mm/5 min × 12), the no-data cells the coverage edge, and of the newest run the steps
whose valid time falls on a ten-minute mark go to `de/nowcast/+<mmm>.png` + `nowcast.json` — the same shape Rainbow's steps ahead have, so the
boards show them as the amber steps to the right of the newest picture (the installation's occ-radar.php reads `nowcast.json` for any source
`sources.json` flags with `nowcast: true`, from v0.37). Licence dl-de/by-2-0, «Source: DWD». The WMS picture of maps.dwd.de (`wms.py` source `de`)
is retired — the same folder, the data instead of a rendered picture. `grids.py --probe --only de` names the runs, the LATEST tar's members and a
member's ODIM attributes. Austria stays with GeoSphere's INCA analysis: the raw radar composite needs an account (see the grids.py header).

## Italy — Radar-DPC, the Protezione Civile's national mosaic (7 Nov, the owner: «Italien go»)

`grids.py` source `it` (kind `dpc`): the platform `radar-api.protezionecivile.it` as its documentation (dpc-radar.readthedocs.io) describes it — `GET /findLastProductByType?type=SRI` names the newest product (`{total, lastProducts:[{productType, time, period}]}`, the time in epoch milliseconds, products every 5 minutes), `POST /downloadProduct` with `{productType, productDate}` answers a pre-signed S3 link to the GeoTIFF, and every call carries the platform's own `origin` header (`https://radar.protezionecivile.it` — the docs say it is required; the probe saw the API answer without it too). The product is **SRI**, the surface rain intensity in mm/h (0.0 where a radar sees dry ground, -9999 beyond the reach — the reach's edge comes from that); **VMI** (maximum reflectivity in dBZ, Marshall-Palmer) stands in when SRI is missing, without a reach (VMI masks everything under 10 dBZ as -9999). The grid: 1200 × 1400 cells of 1 km from (-600000, 650000) in a custom Transverse Mercator (lat_0 42, lon_0 12.5, WGS 84) — the file's GeoKeys name only the datum, so the reader sets the projection itself; onto 0.01° lon/lat over 6–19 E · 35.5–47.5 N. **Licence**: the documentation names «Creative Commons Attribuzione – Condividi allo stesso modo» (CC BY-SA) with «Radar-DPC» to be cited as the source; the pictures of this site carry that line.

The source waits behind `await_probe` until a probe run (`grids.py --probe --only it`: the newest products, the download with and without the origin header, the GeoTIFF's frame and values, SRI's reach drawn as a coarse text picture) has confirmed the reader; then the gate goes and the radar workflow publishes `it` with the rest.

## Retired sources (7 Nov)

The Nordic composite carries the Swedish and Finnish radars, so the single sources `se` (SMHI) and `fi` (FMI) are retired — marked `retired` in their tables, skipped by the runs, their folders removed from the site (`RETIRED` in wms.py). Their code stays; `--only se` or `--only fi` runs one on purpose.


## Rainbow.ai — the AI radar, the precipitation type, the clouds, a nowcast (7 Nov, ready to switch on)

`rainbow.py` fetches Rainbow.ai's products as Web-Mercator XYZ tiles, stitches them and publishes each like every other source — `rb-<area>/` for the precipitation (the picture the boards draw at the bottom of the radar layer; `color=dbz_u8`: the red channel carries dBZ + 32, bit 7 marks snow, a transparent pixel means no radar, so the mosaic is painted in this site's own scale), `rb-<area>-type/` for the **precipitation type** (rain, snow, mixed — Rainbow's own palette), `rb-<area>-clouds/` for the **clouds**. Each folder has `frames.json` (`crs: EPSG:3857`, `kind: precip | type | clouds`), `latest.png`, `status.json`, `coverage.json` (the reach's edge from the transparent pixels; not for clouds) and `usage.json`. The precipitation folder may also hold `nowcast/` + `nowcast.json` (the steps beyond now, when `RAINBOW_FRAMES` asks for them) and `coverage-map.png` + `coverage-map.json` (**where ground radar reaches** — grey — and where only the satellite estimate stands — white; drawn once a day). The boards draw the `precip` kind; the others are listed for them to take up (the installation's owner page names them).

**Off until the secret exists.** Settings → Secrets and variables → Actions → New repository secret → `RAINBOW_API_KEY` (the token from developer.rainbow.ai → Profile). Without it the step logs «no key — skipped» and the run stays green. The key travels in a header, never in a URL or a log.

**Variables** (Settings → Secrets and variables → Actions → Variables; none is needed for the defaults):

| Variable | Default | What it does |
|---|---|---|
| `RAINBOW_LAYERS` | `precip-global,type,clouds` | the products, comma-separated: `precip-global` = the «AI radar» (radar + satellite + nowcast, the oceans too, zoom 0–12) · `precip` = radar + nowcast · `radars` = the radar mosaic alone (zoom 0–7) · `type` = the mosaic painted by precipitation type (zoom 0–7) · `clouds` = cloud cover from satellite (zoom 0–7). The older `RAINBOW_LAYER` (one product) still counts when this is unset. |
| `RAINBOW_AREAS` | `europe:5` | a comma list of `<name>:<zoom>[:<x0>-<x1>:<y0>-<y1>]`; presets `europe:5` (8 × 6 = 48 tiles, 22.5 W – 67.5 E · 21.9 – 66.5 N — the Canaries, Egypt and the Levant inside, ~5 km a pixel), `eu:5` (6 × 5 = 30 tiles, 22.5 W – 45 E · 32 – 66.5 N), `world:3` (8 × 8 = 64 tiles, ~20 km a pixel); several: `europe:5,world:3` |
| `RAINBOW_FRAMES` | `0` | the nowcast steps in minutes for the precipitation products — a list `0,30,60,120,180,240`, a range `0-240/10` (25 steps) or `loop` = one step per `RAINBOW_CADENCE_MIN` up to +240 (the boards' choice, 7 Nov: the steps ahead at the loop's cadence — 9 steps at 30 min, 5 at 60); 0 is the picture of now, the rest go to `nowcast/` |
| `RAINBOW_CADENCE_MIN` | `30` | a new picture only when the newest one is at least this old (the snapshot's clock) — the lever on the cost |
| `RAINBOW_MONTHLY_TILES` | `100000` | the month's tile budget **over every product and area**; reached, the runs skip until the next month and `status.json` says so — never a surprise bill |
| `RAINBOW_COLOR` | `dbz_u8` | the palette asked for the precipitation products; `dbz_u8` is decoded into this site's scale, a number (0–9) keeps Rainbow's own picture |
| `RAINBOW_TYPE_COLOR` | `0` | the palette of the type product |
| `RAINBOW_COVERAGE_MAP` | `1` | once a day the radars' coverage mask of each area as `coverage-map.png` (one area's tiles a day) |

**What it costs** (Rainbow's list as read on 7 Nov: Tiles API 30 000 tiles a month free, then 0.20 $ a thousand; the Nowcast API 5 000 requests free, then 0.10 $ a thousand — theirs to change): tiles a month = products × the area's tiles × steps × runs. With GitHub's schedule the runs come every ten minutes at best, about hourly under load, so the real bill is often below the figure.

| Set-up (europe:5 = 48 tiles) | every 30 min | every 10 min |
|---|---|---|
| the type map alone | ≈ 8 $ | ≈ 35 $ |
| the defaults: AI radar now + type + clouds | ≈ 35 $ | ≈ 118 $ |
| the defaults with a 6-step loop (`0,30,60,120,180,240`) | ≈ 105 $ | ≈ 326 $ |
| the defaults with the 25-step loop (`0-240/10`) | ≈ 367 $ | ≈ 1114 $ |
| `eu:5` (30 tiles) instead of `europe:5` | × 0.63 | × 0.63 |

The clouds are the cheapest to drop (the boards have Meteosat for free); the loop and the cadence are the levers. `usage.json` in every folder keeps the count; the budget variable is the brake.

**First keyed run**: Actions → *probe* → Run workflow logs what each product answers (the snapshot, a tile's size and mode, whether `dbz_u8` is served, whether the forecast axis answers). Should a product not serve `dbz_u8`, set `RAINBOW_COLOR` to a palette number — the picture then comes as Rainbow paints it, the coverage edge still from its transparent pixels.
