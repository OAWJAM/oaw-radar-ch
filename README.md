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


`coverage-union.json` at the site's root is the outline of every precipitation source's reach together — Rainbow.ai's areas among them once they run — and the boards draw that one line under the pictures. Beside it, since 15 Nov (the owner's 320), `coverage-open.json` is the same union **without the Rainbow.ai folders**: the open-data radars alone. The Rainbow calculator on the owner page hatches this one, so an area Rainbow was switched on for never counts as «covered» for the very tiles it is there to buy (the converter itself has always left Rainbow out of the reach it skips tiles for — `coverage.open_data_mask`). When no Rainbow folder runs the two files hold the same rings.

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

## Belgium — RMI's rainfall composite (15 Nov, the owner: «BE / IE bauen»)

The Royal Meteorological Institute publishes its **Belgian rainfall composite** on its open-data GeoServer (`opendata.meteo.be/geoserver/wms`, layer
`belgian_rainfall_composite`, the last thirteen hours every five minutes; the catalogue record RMI_DATASET_BELGIAN_COMPOSITE carries CC BY 4.0). The
WCS and the `/service/radar/wms` path refuse a GetMap (403), only `/geoserver/wms` answers, and it answers a **picture**: source `be` (kind `rmi`) asks one
GetMap per time in plain latitude/longitude over 1 W – 9.7 E · 47.4 – 53.8 N at 0.01° and reads the server's own legend back — six rain classes
(0.6–1.8 · 1.8–10 · 10–30 · 30–50 · 50–500 · 500+ mm/h, each painted at its geometric mean), transparent dry, grey beyond the reach. No key.

## Ireland — Met Éireann's two radars, composed here (15 Nov)

Met Éireann's open radar folder (`opendata2.met.ie/radar/latest/`, CC BY 4.0, no key — it was an FTP with credentials by mail until recently) holds
**polar volumes** of the Shannon (`T_PAGZ40`, 500 m bins to 248 km) and Dublin (`T_PAGZ41`, 1 km bins to 250 km) radars every five minutes and an
hourly accumulation composite, but no five-minute composite. Source `ie` (kind `metie`) composes one: each volume's lowest sweep (0.5°, DBZH) is laid
on the 0.01° grid over 11.5 – 4.5 W · 50.5 – 56 N by great-circle range and bearing from the radar, the stronger echo kept where both see,
Marshall–Palmer to mm/h, below 5 dBZ dry, no data beyond both reaches. The sweeps are the services' «corrected reflectivity»; a radial spoke of
interference may still show now and then.

## The Canary Islands — AEMET's island radars through the Open Radar Data API (15 Nov)

The Canaries lie outside the OPERA composite's grid, and AEMET's own OpenData hands its new georeferenced rasters out only behind a usage limit
(every download of 15 Nov answered 429), but the Open Radar Data API also serves the **single-site volumes** of every OPERA member radar — AEMET's
two island radars among them: **Artenara** on Gran Canaria (`0-724-0-esatn`) and **Buenavista Norte** on Tenerife (`0-724-0-esbnv`), ODIM polar
volumes every ten minutes, CC BY 4.0, no key. Source `ic` (kind `ordscan`) composes them as source `ie` composes Ireland's: the lowest sweep laid on
the 0.01° grid over 18.6 – 12.9 W · 26.9 – 29.7 N by range and bearing, the stronger echo where both see, Marshall–Palmer to mm/h. The reach is
written only from a slot both radars reached. No AEMET key is needed for this; `AEMET_API_KEY` stays for the day the raster endpoints open up.

## Europe — the OPERA composite as the backup layer (15 Nov, the owner: «alle Länder einbinden, die über OPERA liefern — als Backup»)

EUMETNET's **OPERA** programme composes the radars of about thirty European weather services every 15 minutes. Since 20 May 2026 the composite is
open: the **Open Radar Data API** on MeteoGate (`api.meteogate.eu/eu-eumetnet-weather-radar`, an OGC EDR service, no key, CC BY 4.0 — the EU
high-value-datasets rules) lists the last 24 hours and links every file in ECMWF's object store. Source `eu` (kind `opera`) reads the
**NIMBUS instantaneous rain rate composite** (`OPERA@YYYYMMDDTHHMM@0@RATE.h5`, ODIM HDF5, 2 km LAEA, 1 900 × 2 200, nodata −9 999 000 /
undetect −8 888 000) onto 0.02° latitude/longitude over 25 W – 45 E · 31.5 – 72 N. Measured on the 01:30Z picture of 15 Nov the composite carries
the UK, Ireland, Portugal, Spain, Belgium/Luxembourg, Poland, Slovakia, Hungary, Slovenia, Croatia, Bosnia, Serbia, Montenegro, Bulgaria, Romania,
Moldova, the Baltics, Finland, Iceland, Malta and the coast at Tangier/Oran; not Greece, Turkey, Kosovo, North Macedonia, most of Albania or eastern
Ukraine; Cyprus, the Canaries and Madeira lie outside its grid.

It is the **backup layer**: `sources.json` lists it right after Rainbow's folders, so the boards draw it under every national picture, and the
converter **cuts it where a national open-data source is fresh** — its newest frame at most 20 minutes before the newest composite's time (measured
against the composite, not the clock, so a late GitHub cron delays both alike), or 2.5 × the source's own cadence for a slower one (GeoSphere's hourly
analysis) — so a 15-minute composite never draws an echo twice beside a 5-minute national picture; where a national
source is missing (the UK, Iberia, Poland, the Balkans …) or has stopped, OPERA shows. The set of sources it was cut under stands in `eu/cut.json`;
when the set changes, every kept frame is drawn again from the API's 24-hour store. `OPERA_CUT=0` keeps the composite whole. Its `coverage.json`
traces the whole reach (uncut), so the union and the Rainbow calculator count everything OPERA sees. `grids.py --probe --only eu` prints the newest
items and one file's statistics.

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
| `RAINBOW_AREAS` | `europe:5` | a comma list of `<name>:<zoom>[:<x0>-<x1>:<y0>-<y1>]` or, since 15 Nov, `<name>:<zoom>:<x>/<y>+<x>/<y>+…` — a tile list: one area and one layer for the tiles the owner ticked on the installation's calculator, the box their bounding box, the tiles between them not fetched (transparent); presets `europe:5` (8 × 6 = 48 tiles, 22.5 W – 67.5 E · 21.9 – 66.5 N — the Canaries, Egypt and the Levant inside, ~5 km a pixel), `eu:5` (6 × 5 = 30 tiles, 22.5 W – 45 E · 32 – 66.5 N), `world:3` (8 × 8 = 64 tiles, ~20 km a pixel); several: `europe:5,world:3` |
| `RAINBOW_FRAMES` | `0` | the nowcast steps in minutes for the precipitation products — a list `0,30,60,120,180,240`, a range `0-240/10` (25 steps) or `loop` = one step per `RAINBOW_CADENCE_MIN` up to +240 (the boards' choice, 7 Nov: the steps ahead at the loop's cadence — 9 steps at 30 min, 5 at 60); 0 is the picture of now, the rest go to `nowcast/` |
| `RAINBOW_CADENCE_MIN` | `30` | a new picture only when the newest one is at least this old (the snapshot's clock) — the lever on the cost |
| `RAINBOW_OFF` | `0` | `1` switches the source off: the run skips Rainbow and removes the `rb-…` folders from the site (the open-data radars stay); delete the variable to switch it on again (the key must still exist) |
| `RAINBOW_MONTHLY_TILES` | `9500` | the month's **call** budget over every product and area (tiles and snapshot asks together); reached, the runs skip until the next month and `status.json` says so — never a surprise bill, and under Rainbow's Free plan (10 000 calls a month, 16 Nov) the service never pauses |
| `RAINBOW_COLOR` | `dbz_u8` | the palette asked for the precipitation products; `dbz_u8` is decoded into this site's scale, a number (0–9) keeps Rainbow's own picture |
| `RAINBOW_TYPE_COLOR` | `0` | the palette of the type product |
| `RAINBOW_COVERAGE_MAP` | `1` | once a day the radars' coverage mask of each area as `coverage-map.png` (one area's tiles a day) |

**What it costs** (Rainbow's plans as read on 16 Nov — the legacy pay-as-you-go plan is discontinued): **Free** 10 000 calls a month, no card, the service pauses at the limit; **Starter** 260 000 calls for 25 $ a month, **Growth** 1.1 million for 100 $, **Pro** 3 million for 250 $ — on the paid plans a tile counts as two calls (theirs to change). Calls a month = products × (the area's tiles × steps × pictures + the snapshot asks). Since 16 Nov the snapshot is asked only when a picture can be due (the kept picture's age against the clock), about two asks a picture, so a run every ten minutes costs nothing between pictures.

| Set-up | calls a month | plan |
|---|---|---|
| a tile list of 39, one product, every 4 h (6 pictures a day) | ≈ 7 400 | Free (≈ 74 %) |
| the same every 3 h | ≈ 9 700 | too close to Free's 10 000 |
| the same every hour | ≈ 29 000 | Starter |
| `europe:5` (48 tiles), the defaults: AI radar now + type + clouds, every 30 min | ≈ 212 000 tiles = 424 000 calls | Growth |
| the defaults with the 25-step loop (`0-240/10`) | ≈ 5.2 million tiles | — |

The clouds are the cheapest to drop (the boards have Meteosat for free); the cadence, the tile list and the loop are the levers. `usage.json` in every folder keeps the count; the budget variable is the brake. An area or product taken out of the variables leaves the site at the next run (its folder is removed, its tiles of the month stay counted).

**First keyed run**: Actions → *probe* → Run workflow logs what each product answers (the snapshot, a tile's size and mode, whether `dbz_u8` is served, whether the forecast axis answers). Should a product not serve `dbz_u8`, set `RAINBOW_COLOR` to a palette number — the picture then comes as Rainbow paints it, the coverage edge still from its transparent pixels.
