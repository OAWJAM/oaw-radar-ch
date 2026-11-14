#!/usr/bin/env python3
"""The national precipitation pictures that come as a georeferenced grid rather than a WMS — SMHI's Swedish radar composite (a GeoTIFF in
SWEREF99 TM, EPSG:3006) and GeoSphere Austria's INCA precipitation analysis (a NetCDF on the 1 km Austria Lambert grid, EPSG:31287) —
reprojected onto plain latitude/longitude and painted with the same scale as the Swiss picture, under <out>/<id>/ like the WMS sources.

  python3 grids.py [--out site] [--frames 12] [--only se,at] [--backfill 12] [--probe]

Sources (keyless, open data; since 7 Nov also ČHMÚ's open-data portal — the Czech MAX_Z composite as ODIM HDF5, Mercator 1 km, every 5 min):
  SMHI       https://opendata-download-radar.smhi.se/api   version/latest/area/sweden/product/comp — a PNG and a GeoTIFF every five minutes;
             pixel value v (1…254) → dBZ = 0.4·v − 30 (0 = no echo, 255 = no data); rain rate by Marshall–Palmer Z = 200·R^1.6.
  GeoSphere  https://dataset.api.hub.geosphere.at/v1/grid/forecast/nowcast-v1-15min-1km — the INCA nowcast, a run every 15 minutes; its
             first step (lead 0) is the INCA precipitation analysis: the Austrian radar composite blended with the rain gauges, 1 km,
             rr in kg m-2 per 15 minutes. The plain ask clips the past steps away; start=end=<run time> hands out the analysis step, and
             forecast_offset=k the k-th run before the newest (max_forecast_offset 5) — so each pass backfills the last 90 minutes.
             The raw radar itself (inca-v1-15min-1km, historical) needs an account and is not used.
"""
import argparse, datetime as dt, io, json, os, re, sys, time
import numpy as np
import requests
from PIL import Image
from pyproj import CRS, Transformer
import convert

UA = convert.UA
SMHI = 'https://opendata-download-radar.smhi.se/api/version/latest/area/sweden/product/comp'
GEO = 'https://dataset.api.hub.geosphere.at/v1'
INCA = GEO + '/grid/forecast/nowcast-v1-15min-1km'
SOURCES = [
    {'id': 'se', 'kind': 'smhi', 'name': 'Sweden', 'source': 'SMHI', 'licence': 'CC BY 4.0, Source: SMHI', 'product': 'COMP', 'bbox': (9.5, 54.5, 25.0, 69.5), 'step_min': 5,
     'retired': 'the Nordic composite (no) carries the Swedish radars since 7 Nov (the owner, 6 Nov 23:18: «20. ersetzen»)'},
    {'id': 'at', 'kind': 'geosphere', 'name': 'Austria', 'source': 'GeoSphere Austria', 'licence': 'CC BY 4.0, Source: GeoSphere Austria', 'product': 'INCA analysis',
     'bbox': (8.1, 45.5, 17.75, 49.5), 'step_min': 15, 'note': 'INCA precipitation analysis: the radar composite blended with the rain gauges (1 km, every 15 min), the nowcast\'s lead-0 step'},
    {'id': 'dk', 'kind': 'dmi', 'name': 'Denmark', 'source': 'DMI', 'licence': 'CC BY 4.0, Source: DMI', 'product': 'Radar composite', 'bbox': (4.5, 52.5, 20.5, 59.5), 'step_min': 5, 'step': 0.02,
     'note': 'DMI\'s radar composite from the open data API (no key since 2026 — «neither registration, nor API key are needed»), ODIM HDF5 every 5 min (6 Nov, late)'},
    {'id': 'nl', 'kind': 'knmi', 'name': 'Netherlands', 'source': 'KNMI', 'licence': 'CC BY 4.0, Source: KNMI', 'product': 'Radar reflectivity composite', 'bbox': (0.0, 49.5, 10.5, 55.5), 'step_min': 5, 'step': 0.02,
     'secret': 'KNMI_API_KEY', 'note': 'KNMI\'s radar reflectivity composite (RAD_NL25_PCP_NA, 1 km, every 5 min) from the Data Platform — the key is the repository secret KNMI_API_KEY (6 Nov, late)'},
    {'id': 'fr', 'kind': 'meteofrance', 'name': 'France', 'source': 'M\u00e9t\u00e9o-France', 'licence': 'Licence Ouverte 2.0, Source: M\u00e9t\u00e9o-France', 'product': 'Lame d\'eau 500 m (5-min rainfall, as mm/h)', 'bbox': (-6.0, 41.0, 10.5, 51.5), 'step_min': 5, 'step': 0.02,
     'secret': 'METEOFRANCE_APP_ID', 'note': 'M\u00e9t\u00e9o-France\'s Paquet Radar API (a tar.gz every quarter hour with three 5-min steps): the metropolitan 500 m lame d\'eau T_IPRN20 (ODIM HDF5, polar stereographic, 3472\u00b2) read since 7 Nov 22:45; the application id is the repository secret METEOFRANCE_APP_ID'},
    {'id': 'no', 'kind': 'nordic', 'name': 'Norway · Sweden · Finland (Nordic composite)', 'source': 'MET Norway', 'licence': 'CC BY 4.0, Source: MET Norway', 'product': 'Nordic radar composite (pcappi 0 dBZ)',
     'bbox': (0.5, 54.0, 38.0, 72.0), 'step_min': 5, 'step': 0.02,
     'note': 'The Nordic composite from MET Norway\'s THREDDS server — the radars of Norway, Sweden and Finland on one 1 km Lambert grid every 5 min (6 Nov, late: data, not the rendered picture of api.met.no)'},
    {'id': 'es', 'kind': 'aemet', 'name': 'Spain', 'source': 'AEMET', 'licence': 'AEMET OpenData, Source: AEMET', 'product': 'National radar composite (picture)', 'bbox': (-10.0, 35.0, 5.0, 44.5), 'step_min': 10, 'step': 0.02,
     'secret': 'AEMET_API_KEY', 'probe_only': True,
     'note': 'AEMET\'s national radar composite from OpenData (api/red/radar/nacional) — a picture, georeferenced by hand; the key is the repository secret AEMET_API_KEY (7 Nov 22:39; the first pass keeps the raw picture as es/_probe.png for the calibration)'},
    {'id': 'is', 'kind': 'imo', 'name': 'Iceland', 'source': 'Icelandic Meteorological Office', 'licence': 'Free use with attribution (IMO conditions of use), Source: Icelandic Meteorological Office', 'product': 'Radar composite Keflav\u00edk \u00b7 Bj\u00f3lfur \u00b7 Skaga (reflectivity, as mm/h)', 'bbox': (-29.5, 62.8, -9.5, 67.4), 'step_min': 5, 'step': 0.02,
     'note': 'the IMO radar composite picture (vedur.is, every 5 min) read by its legend and placed by its graticule — a Lambert conformal conic fitted from the picture (8 Nov 00:30); the owner\'s «Ja» of 00:17'},
    {'id': 'it', 'kind': 'dpc', 'name': 'Italy', 'source': 'Radar-DPC', 'licence': 'CC BY-SA, Source: Radar-DPC (Dipartimento della Protezione Civile)', 'product': 'National radar mosaic (SRI, rain intensity; VMI when SRI is missing)',
     'bbox': (6.0, 35.5, 19.0, 47.5), 'step_min': 5, 'step': 0.01,
     'note': 'Radar-DPC, the national mosaic of the Dipartimento della Protezione Civile (radar-api.protezionecivile.it, docs dpc-radar.readthedocs.io): the newest product as a GeoTIFF on a 1 km Transverse Mercator grid, asked with the platform\'s own origin header; the docs name CC BY-SA with «Radar-DPC» as the source (7 Nov, the owner: «Italien go»)'},
    {'id': 'de', 'kind': 'dwd', 'name': 'Germany', 'source': 'DWD', 'licence': 'dl-de/by-2-0, Source: DWD', 'product': 'RV composite (5-min precipitation, RADVOR steps ahead)',
     'bbox': (1.4, 45.6, 18.8, 55.9), 'step_min': 5, 'step': 0.01,
     'note': 'DWD\'s RV composite from the open-data server (opendata.dwd.de/weather/radar/composite/rv, one tar per run with ODIM HDF5 members: the analysis and the RADVOR steps +5 \u2026 +120 min, 1 km polar-stereographic, every 5 min) \u2014 the data, not the WMS picture (14 Nov, the owner: \u00abRadar de/at ja\u00bb); the steps that fall on a ten-minute mark go to nowcast/ as the boards\u2019 steps ahead'},
    {'id': 'cz', 'kind': 'chmi', 'name': 'Czechia', 'source': '\u010cHM\u00da', 'licence': 'CC BY 4.0 (the \u010cHM\u00da open-data page carries the CC BY 4.0 mark; the data server itself names none), Source: \u010cHM\u00da', 'product': 'MAX_Z composite',
     'bbox': (11.2, 48.0, 19.7, 51.5), 'step_min': 5, 'step': 0.01,
     'note': '\u010cHM\u00da\'s radar composite (maximum reflectivity, 1 km Mercator, every 5 min) from the open-data portal opendata.chmi.cz — ODIM HDF5, no key (7 Nov, the owner: «CZ ja»)'},
]
DMI = 'https://opendataapi.dmi.dk/v1/radardata'
KNMI = 'https://api.dataplatform.knmi.nl/open-data/v1/datasets/radar_reflectivity_composites/versions/2.0/files'
MF_TOKEN = 'https://portail-api.meteofrance.fr/token'
MF_PAQUET = 'https://public-api.meteofrance.fr/public/DPPaquetRadar/v1/mosaique/paquet'
AEMET = 'https://opendata.aemet.es/opendata/api/red/radar/nacional'
THREDDS = 'https://thredds.met.no/thredds'
CHMI = 'https://opendata.chmi.cz/meteorology/weather/radar/composite/maxz/hdf5/'
CHMI_ROOT = 'https://opendata.chmi.cz/'
DWD_RV = 'https://opendata.dwd.de/weather/radar/composite/rv/'          # DWD's RV runs: composite_rv_YYYYMMDD_HHMM.tar (+ composite_rv_LATEST.tar), ODIM HDF5 inside (14 Nov)
DPC_API = 'https://radar-api.protezionecivile.it'                   # Radar-DPC (dpc-radar.readthedocs.io): findLastProductByType, downloadProduct (a pre-signed S3 link), the origin header on every call
DPC_ORIGIN = 'https://radar.protezionecivile.it'                   # the platform's own page: «è necessario includere il parametro origin in tutte le richieste» (7 Nov, the owner: «Italien go»)
DPC_TERMS = 'https://dpc-radar.readthedocs.io/it/latest/'
DPC_TM = '+proj=tmerc +lat_0=42 +lon_0=12.5 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs'   # the custom Transverse Mercator of VMI/SRI as the docs' gdalinfo shows it (1200 × 1400 at 1 km from -600000/650000)
NORDIC_CAT = THREDDS + '/catalog/remotesensing/reflectivity-nordic/latest/catalog.html'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def get(url, **kw):
    headers = {'User-Agent': UA, 'Accept': 'application/json, */*'}; headers.update(kw.pop('headers', {}) or {})
    r = requests.get(url, headers=headers, timeout=90, **kw)
    r.raise_for_status()
    return r


def smhi_files(n=12):
    """The newest n GeoTIFFs of the composite, oldest first: [(stamp, href)]. The product's metadata names only the last file or two, so the
    day's listing (?format=tif; yesterday's too around midnight) is read for the rest."""
    files = {}
    def take(lst):
        for f in lst or []:
            if not isinstance(f, dict):
                continue
            valid = f.get('valid') or f.get('validTime') or ''
            for fm in (f.get('formats') or []):
                if str(fm.get('key', '')).lower() in ('tif', 'tiff', 'geotiff') and fm.get('link') and valid:
                    try:
                        files[parse_time(str(valid))] = fm['link']
                    except Exception:
                        pass
    try:
        j = get(SMHI).json()
        take(j.get('lastFiles')); take(j.get('files'))
    except Exception as e:
        log('smhi metadata not read:', e)
    now = dt.datetime.now(dt.timezone.utc)
    for day in (now, now - dt.timedelta(days=1)):
        if len([t for t in files if t >= now - dt.timedelta(hours=2)]) >= n:
            break
        try:
            take(get(SMHI + day.strftime('/%Y/%m/%d'), params={'format': 'tif'}).json().get('files'))
        except Exception as e:
            log('smhi day listing not read:', e)
    return [(t, files[t]) for t in sorted(files)][-n:]


def smhi_latest():
    """The newest GeoTIFF of the composite: (stamp, href), or None."""
    lst = smhi_files(1)
    return lst[-1] if lst else None


def parse_time(s):
    s = s.strip().replace('Z', '+00:00')
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        t = dt.datetime.fromisoformat(s.split('.')[0] + '+00:00')
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)


def read_geotiff(blob: bytes):
    """The first band and its affine frame (origin x/y of the top-left corner, pixel size) and the EPSG code from the GeoTIFF tags."""
    import tifffile
    with tifffile.TiffFile(io.BytesIO(blob)) as tf:
        page = tf.pages[0]
        data = page.asarray()
        tags = {t.name: t.value for t in page.tags.values()}
    if data.ndim == 3:
        data = data[..., 0]
    scale = tags.get('ModelPixelScaleTag'); tie = tags.get('ModelTiepointTag'); tr = tags.get('ModelTransformationTag')
    if tr is not None:
        m = np.array(tr, dtype=float).reshape(4, 4)
        x0, y0, xs, ys = m[0, 3], m[1, 3], m[0, 0], -m[1, 1]
    elif scale is not None and tie is not None:
        xs, ys = float(scale[0]), float(scale[1])
        i, jj, _, X, Y = (float(v) for v in tie[:5])
        x0, y0 = X - i * xs, Y + jj * ys
    else:
        raise RuntimeError('the GeoTIFF names no georeference')
    epsg = None
    keys = tags.get('GeoKeyDirectoryTag')
    if keys is not None:
        k = list(keys)
        for n in range(4, len(k) - 3, 4):
            if k[n] in (3072, 2048) and k[n + 1] == 0:      # ProjectedCSTypeGeoKey / GeographicTypeGeoKey
                epsg = int(k[n + 3])
                if k[n] == 3072:
                    break
    if epsg in (None, 0, 32767):                       # user-defined in the file: SMHI documents SWEREF99 TM
        epsg = 3006
    return data, {'x0': x0, 'y0': y0, 'xs': xs, 'ys': ys, 'epsg': epsg, 'nodata': tags.get('GDAL_NODATA'), 'ascii': str(tags.get('GeoAsciiParamsTag', ''))[:200], 'keys': list(keys)[:40] if keys is not None else None}


def smhi_rate(data: np.ndarray):
    """SMHI's 8-bit composite → mm/h (NaN where no data)."""
    v = data.astype(np.float32)
    dbz = 0.4 * v - 30.0
    rate = (np.power(10.0, dbz / 10.0) / 200.0) ** (1.0 / 1.6)
    rate[data == 0] = 0.0
    rate[data == 255] = np.nan
    rate[dbz < 5.0] = 0.0                      # below 5 dBZ: no rain worth painting
    return rate


def reproject_grid(rate, frame, bbox, step):
    W, S, E, N = bbox
    nx = int(round((E - W) / step)); ny = int(round((N - S) / step))
    lons = W + (np.arange(nx) + 0.5) * step
    lats = N - (np.arange(ny) + 0.5) * step
    LON, LAT = np.meshgrid(lons, lats)
    to_grid = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(frame['crs']) if frame.get('crs') else CRS.from_epsg(frame['epsg']), always_xy=True)   # an EPSG code or a proj4 string (the Nordic LCC, 6 Nov)
    X, Y = to_grid.transform(LON, LAT)
    col = np.floor((X - frame['x0']) / frame['xs']).astype(np.int64)
    row = np.floor((frame['y0'] - Y) / frame['ys']).astype(np.int64)
    h, w = rate.shape
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    out = np.full((ny, nx), np.nan, dtype=np.float32)
    out[inside] = rate[row[inside], col[inside]]
    return out, (nx, ny)


def write_frames(out, src, stamp, img, keep, extra):
    d = out + '/' + src['id']
    os.makedirs(d + '/frames', exist_ok=True)
    key = stamp.strftime('%Y%m%d%H%M')
    img.save(d + '/frames/' + key + '.png', optimize=True)
    img.save(d + '/latest.png', optimize=True)
    frames = sorted(f for f in os.listdir(d + '/frames') if re.match(r'^\d{12}\.png$', f))
    for f in frames[:-keep] if len(frames) > keep else []:
        os.remove(d + '/frames/' + f)
    frames = frames[-keep:]
    W, S, E, N = src['bbox']
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump({'product': src['product'], 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:4326', 'updated': now, 'source': src['source'], 'licence': src['licence'],
               'legend': [{'from_mmh': (convert.SCALE[i - 1][0] if i else 0.1), 'to_mmh': (hi if hi < 1e8 else None), 'rgba': list(c)} for i, (hi, c) in enumerate(convert.SCALE)],
               'frames': [{'time': f[:4] + '-' + f[4:6] + '-' + f[6:8] + 'T' + f[8:10] + ':' + f[10:12] + 'Z', 'src': 'frames/' + f} for f in frames]} | extra,
              open(d + '/frames.json', 'w'), indent=1)
    json.dump({'ok': True, 'error': '', 'at': now, 'latest': stamp.strftime('%Y-%m-%dT%H:%MZ')}, open(d + '/status.json', 'w'), indent=1)
    return key, len(frames)


def geosphere_runs():
    """The INCA nowcast's runs on offer, newest first: [(run time, forecast_offset)] — offset k asks for the k-th run before the newest."""
    j = get(INCA + '/metadata').json()
    refs = []
    for s in (j.get('available_forecast_reftimes') or []):
        try:
            refs.append(parse_time(str(s)))
        except Exception:
            pass
    if not refs and j.get('last_forecast_reftime'):
        refs.append(parse_time(str(j['last_forecast_reftime'])))
    refs = sorted(set(refs), reverse=True)
    return [(t, k) for k, t in enumerate(refs)]


def geosphere_analysis(run, offset, bbox):
    """One run's analysis step (lead 0) as (valid time, rate mm/h on the 1 km grid, frame, lead hours): start=end=<run time> picks that step,
    forecast_offset the run. The NetCDF's rr is the 15-minute sum in kg m-2 (scaled int16, -999 missing) on EPSG:31287 — x/y in metres."""
    import netCDF4
    W, S, E, N = bbox
    f = run.strftime('%Y-%m-%dT%H:%M')
    params = {'parameters': 'rr', 'bbox': '%s,%s,%s,%s' % (S, W, N, E), 'output_format': 'netcdf', 'start': f, 'end': f}
    if offset:
        params['forecast_offset'] = offset
    r = requests.get(INCA, params=params, headers={'User-Agent': UA, 'Accept': 'application/x-netcdf, */*'}, timeout=180)
    r.raise_for_status()
    ds = netCDF4.Dataset('inmemory.nc', memory=r.content)
    t = ds.variables['time']
    m = re.match(r'(\w+)\s+since\s+(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?', str(getattr(t, 'units', '')))
    if not m:
        raise RuntimeError('time units not understood: ' + str(getattr(t, 'units', '')))
    base = parse_time(m.group(2) + 'T' + m.group(3) + ':00')
    mult = {'days': 86400, 'hours': 3600, 'minutes': 60, 'seconds': 1}.get(m.group(1).lower())
    if not mult:
        raise RuntimeError('time units not understood: ' + str(t.units))
    tv = np.ravel(np.ma.filled(t[:], np.nan))
    if tv.size == 0:
        raise RuntimeError('no time step in the answer')
    valid = base + dt.timedelta(seconds=float(tv[0]) * mult)
    lead = float(np.ravel(np.ma.filled(ds.variables['leadtime'][:], 0))[0]) if 'leadtime' in ds.variables else float((valid - run).total_seconds() / 3600)
    rr = ds.variables['rr'][0]                        # masked, scale_factor applied → mm per 15 min
    x = np.asarray(ds.variables['x'][:], dtype=float); y = np.asarray(ds.variables['y'][:], dtype=float)
    data = np.ma.filled(np.ma.asarray(rr).astype(np.float32), np.nan)
    if data.ndim != 2 or x.size < 2 or y.size < 2:
        raise RuntimeError('unexpected grid %s' % (data.shape,))
    if y[1] > y[0]:                                   # row 0 must be the north
        data = data[::-1]; y = y[::-1]
    xs = float(abs(x[1] - x[0])); ys = float(abs(y[0] - y[1]))
    frame = {'x0': float(x[0]) - xs / 2, 'y0': float(y[0]) + ys / 2, 'xs': xs, 'ys': ys, 'epsg': 31287}
    rate = data * 4.0                                 # the 15-minute sum → mm/h
    rate[rate < 0.1] = 0.0
    return valid.astimezone(dt.timezone.utc).replace(second=0, microsecond=0), rate, frame, lead


def run_geosphere(src, a):
    """The Austrian source: every run on offer whose analysis is not yet a frame — oldest first, so latest.png ends with the newest."""
    d = a.out + '/' + src['id']
    runs = geosphere_runs()
    if not runs:
        raise RuntimeError('no run named by the service')
    todo = [(t, k) for t, k in runs[:max(1, a.backfill)] if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        log(src['id'], 'already there', runs[0][0].strftime('%Y%m%d%H%M')); return
    n = 0
    for run, k in sorted(todo):
        try:
            try:
                valid, rate, frame, lead = geosphere_analysis(run, k, src['bbox'])
            except RuntimeError as e:
                if 'no time step' not in str(e):
                    raise
                # a newer run appeared while this pass ran, so every offset moved by one: read the list again and ask once more
                k2 = dict((t, kk) for t, kk in geosphere_runs()).get(run)
                if k2 is None or k2 == k:
                    raise
                log(src['id'], 'run', run.strftime('%H:%MZ'), 'is now offset', k2, '— asked again')
                valid, rate, frame, lead = geosphere_analysis(run, k2, src['bbox'])
        except Exception as e:
            log(src['id'], 'run', run.strftime('%H:%MZ'), 'offset', k, 'failed:', e); continue
        if abs(lead) > 1e-6 or valid != run:
            log(src['id'], 'run', run.strftime('%H:%MZ'), 'offset', k, 'gave lead', lead, 'valid', valid.strftime('%H:%MZ'), '— not the analysis, skipped'); continue
        if os.path.exists(d + '/frames/' + valid.strftime('%Y%m%d%H%M') + '.png'):
            continue
        field, size = reproject_grid(rate, frame, src['bbox'], a.step)
        img = convert.paint(field)
        wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
        key, cnt = write_frames(a.out, src, valid, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': a.step, 'url': INCA, 'note': src.get('note', ''), 'lead_h': 0})
        log('  done', key, size, 'wet share', round(wet, 4), 'frames', cnt, 'grid', rate.shape, 'offset', k)
        n += 1
    if n == 0:
        raise RuntimeError('no analysis step read')


def odim_rate(rate, quantity, acc_min=5):
    """An ODIM field as mm/h: a RATE/RR field as it is; an accumulation (ACRR, mm over acc_min minutes — Météo-France's lame d'eau is 5 min,
    7 Nov 22:45) scaled to the hour; a reflectivity field (DBZH, TH …) through Marshall-Palmer (Z = 200 R^1.6), below 5 dBZ dry."""
    q = str(quantity or '').upper()
    if q.startswith('DBZ') or q in ('TH', 'TV', 'ZH'):
        r = (np.power(10.0, rate / 10.0) / 200.0) ** (1.0 / 1.6)
        r[rate < 5.0] = 0.0
        r[~np.isfinite(rate)] = np.nan
        return r.astype(np.float32)
    if q == 'ACRR' and acc_min and acc_min > 0:
        return (rate * (60.0 / float(acc_min))).astype(np.float32)
    return rate


def dmi_items(n=12):
    """The newest n composite files of DMI's radar data API, oldest first: [(stamp, href)]. The items of the last two hours, the asset's download link."""
    now = dt.datetime.now(dt.timezone.utc)
    r = get(DMI + '/collections/composite/items', params={'limit': 200, 'datetime': (now - dt.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ') + '/' + now.strftime('%Y-%m-%dT%H:%M:%SZ')})
    out = {}
    for ft in (r.json().get('features') or []):
        props = ft.get('properties') or {}
        t = props.get('datetime') or props.get('created') or ''
        assets = ft.get('assets') or ft.get('asset') or {}
        href = ''
        for k, v in (assets.items() if isinstance(assets, dict) else []):
            if isinstance(v, dict) and v.get('href'):
                href = v['href']; break
        if not href or not t:
            continue
        try:
            out[parse_time(str(t))] = href
        except Exception:
            pass
    return [(t, out[t]) for t in sorted(out)][-n:]


def run_dmi(src, a):
    d = a.out + '/' + src['id']
    step = src.get('step', a.step)
    items = dmi_items(max(1, a.backfill))
    if not items:
        raise RuntimeError('no composite item in the last two hours')
    todo = [(t, h) for t, h in items if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        log(src['id'], 'already there', items[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0
    for stamp, href in todo:
        try:
            blob = get(href).content
            rate, grid, scale, quantity = convert.read_odim(blob)
            rate = odim_rate(rate, quantity)
            field, _, size = convert.reproject(rate, grid, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': href, 'note': src.get('note', ''), 'quantity': quantity})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'quantity', quantity); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def chmi_files(n=12):
    """The newest n MAX_Z composites of ČHMÚ's open-data portal, oldest first: [(stamp, url)] — the folder's index page names the files
    T_PABV23_C_OKPR_YYYYMMDDhhmmss.hdf (the timestamp UTC); a JSON listing is read too should the portal offer one."""
    r = get(CHMI, headers={'User-Agent': UA, 'Accept': 'text/html, application/json, */*'})
    body = r.text
    found = {}
    for m in re.finditer(r'(T_PABV23_C_OKPR_(\d{14})\.hdf)', body):
        try:
            t = dt.datetime.strptime(m.group(2), '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        found[t] = CHMI + m.group(1)
    stamps = sorted(found)
    return [(t, found[t]) for t in stamps[-n:]]


def run_chmi(src, a):
    """The Czech source: the newest composites not yet a frame, oldest first — ODIM HDF5 read as DMI's, the reflectivity through Marshall-Palmer."""
    d = a.out + '/' + src['id']
    step = src.get('step', a.step)
    items = chmi_files(max(1, a.backfill))
    if not items:
        raise RuntimeError('no composite file named on the portal\'s index page')
    todo = [(t, h) for t, h in items if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        log(src['id'], 'already there', items[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0
    for stamp, href in todo:
        try:
            blob = get(href).content
            rate, grid, scale, quantity = convert.read_odim(blob)
            rate = odim_rate(rate, quantity)
            field, _, size = convert.reproject(rate, grid, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': href, 'note': src.get('note', ''), 'quantity': quantity})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'quantity', quantity); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def purge_other_frames(d, bbox, step):
    """Frames of another size than this source's grid — left by a source that drew the same folder before (the WMS picture until 14 Nov) — are removed,
    so the list never stretches an old picture over the new bounding box."""
    W, S, E, N = bbox
    want = (int(round((E - W) / step)), int(round((N - S) / step)))
    fd = d + '/frames'
    if not os.path.isdir(fd):
        return 0
    n = 0
    for f in os.listdir(fd):
        if not re.match(r'^\d{12}\.png$', f):
            continue
        try:
            with Image.open(fd + '/' + f) as im:
                size = im.size
        except Exception:
            size = None
        if size != want:
            os.remove(fd + '/' + f); n += 1
    if n:
        log('  removed', n, 'frame(s) of another size (a former source\'s)', 'wanted', want)
    return n


def dwd_runs(n=12):
    """The newest n RV runs of DWD's open-data folder, oldest first: [(stamp, url)] — the folder's index page names composite_rv_YYYYMMDD_HHMM.tar
    (the run's analysis time, UTC) beside composite_rv_LATEST.tar."""
    r = get(DWD_RV, headers={'User-Agent': UA, 'Accept': 'text/html, */*'})
    found = {}
    for m in re.finditer(r'(composite_rv_(\d{8})_(\d{4})\.tar)(?!\.)', r.text):
        try:
            t = dt.datetime.strptime(m.group(2) + m.group(3), '%Y%m%d%H%M').replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        found[t] = DWD_RV + m.group(1)
    stamps = sorted(found)
    return [(t, found[t]) for t in stamps[-n:]]


def dwd_members(blob):
    """The members of an RV run's tar as {lead_min: bytes} — composite_rv_YYYYMMDD_HHMM_<lll>-hd5, 000 the analysis, the rest the RADVOR steps."""
    import tarfile
    out = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as tar:
        for m in tar.getmembers():
            mm = re.search(r'_(\d{3})-hd5$', m.name)
            if not mm or not m.isfile():
                continue
            f = tar.extractfile(m)
            if f is not None:
                out[int(mm.group(1))] = f.read()
    return out


def dwd_field(blob, src, step):
    """One ODIM member → the mm/h field on the source's plate-carrée grid (ACRR over 5 min scaled to the hour), its size and the wet share."""
    rate, grid, scale, quantity = convert.read_odim(blob)
    rate = odim_rate(rate, quantity, 5)
    field, _, size = convert.reproject(rate, grid, src['bbox'], step)
    field[np.isfinite(field) & (field < 0.1)] = 0.0
    wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
    return field, size, wet, quantity


def write_nowcast_steps(d, stamp, steps, source, note):
    """The steps beyond now of one run (the same shape Rainbow's nowcast has, 7 Nov): nowcast/+<mmm>.png and nowcast.json; the previous run's files go."""
    nd = d + '/nowcast'
    os.makedirs(nd, exist_ok=True)
    keep = set()
    for lead, img in steps:
        f = '+%03d.png' % lead
        img.save(nd + '/' + f, optimize=True); keep.add(f)
    for f in os.listdir(nd):
        if f.endswith('.png') and f not in keep:
            os.remove(nd + '/' + f)
    json.dump({'issued': stamp.strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': source,
               'frames': [{'lead_min': lead, 'valid': (stamp + dt.timedelta(minutes=lead)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'src': 'nowcast/+%03d.png' % lead} for lead, _ in steps],
               'note': note}, open(d + '/nowcast.json', 'w'), indent=1)


def run_dwd(src, a):
    """The German source (14 Nov): the newest RV runs not yet a frame, oldest first — the analysis member as the picture, the RADVOR steps of the
    newest run that fall on a ten-minute mark as the steps ahead (nowcast/), the reach from the analysis' no-data."""
    d = a.out + '/' + src['id']
    step = src.get('step', a.step)
    if a.file:
        m = re.search(r'(\d{8})_(\d{4})', os.path.basename(a.file))
        stamp = dt.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M').replace(tzinfo=dt.timezone.utc) if m else dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        items = [(stamp, a.file)]
    else:
        items = dwd_runs(max(1, a.backfill))
    if not items:
        raise RuntimeError('no run named on the folder\'s index page')
    purge_other_frames(d, src['bbox'], step)                                                         # the WMS picture's frames of the hour before the switch (another size): gone, not stretched
    todo = [(t, h) for t, h in items if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        log(src['id'], 'already there', items[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0; newest = items[-1][0]
    for stamp, href in todo:
        try:
            blob = open(href, 'rb').read() if a.file else get(href).content
            members = dwd_members(blob)
            if 0 not in members:
                raise RuntimeError('no analysis member (_000) in the tar: ' + ', '.join(str(k) for k in sorted(members))[:80])
            field, size, wet, quantity = dwd_field(members[0], src, step)
            img = convert.paint(field)
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            ahead = []
            if stamp == newest:                                                                    # the steps ahead of the newest run alone: every lead whose valid time sits on a ten-minute mark
                for lead in sorted(k for k in members if k > 0):
                    if (stamp + dt.timedelta(minutes=lead)).minute % 10 != 0 or lead > 240:
                        continue
                    try:
                        f2, _, _, _ = dwd_field(members[lead], src, step)
                        ahead.append((lead, convert.paint(f2)))
                    except Exception as e:
                        log('  step +%d failed:' % lead, e)
                if ahead:
                    write_nowcast_steps(d, stamp, ahead, src['source'], 'DWD’s RADVOR steps of the run named (the RV composite’s forecast members): the picture of each step as it stood then — a forecast, not an observation')
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': href, 'note': src.get('note', ''), 'quantity': quantity,
                                                                 'members': len(members), 'nowcast_steps': [l for l, _ in ahead]})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'members', len(members), 'steps ahead', [l for l, _ in ahead]); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def probe_dwd():
    """DWD's RV folder: the newest runs named, the LATEST tar's members, one member's ODIM attributes and the field's values."""
    try:
        runs = dwd_runs(6)
        log('rv runs named:', [(t.strftime('%Y-%m-%d %H:%MZ'), u.rsplit('/', 1)[1]) for t, u in runs])
    except Exception as e:
        log('the folder index not read:', e); runs = []
    try:
        r = get(DWD_RV + 'composite_rv_LATEST.tar')
        log('LATEST.tar', len(r.content), 'bytes, content-type', r.headers.get('content-type'), 'last-modified', r.headers.get('last-modified'))
        members = dwd_members(r.content)
        log('members (lead min):', sorted(members), 'sizes', {k: len(v) for k, v in sorted(members.items())[:4]})
        for lead in (0, 5, 60):
            if lead in members:
                rate, grid, scale, quantity = convert.read_odim(members[lead])
                r2 = odim_rate(rate, quantity, 5)
                log('  +%03d' % lead, 'quantity', quantity, 'scale', scale, 'finite share', round(float(np.isfinite(r2).mean()), 3), 'wet share', round(float(np.nanmean(r2 >= 0.1)), 4), 'max mm/h', round(float(np.nanmax(r2)), 1))
    except Exception as e:
        log('LATEST.tar not read:', e)
    return 0


def dpc_headers(extra=None):
    h = {'User-Agent': UA, 'Accept': 'application/json, */*', 'Origin': DPC_ORIGIN, 'Referer': DPC_ORIGIN + '/'}
    h.update(extra or {})
    return h


def dpc_last(product):
    """The newest product's time (UTC) as the platform names it: findLastProductByType → {total, lastProducts:[{productType, time (ms), period}]}."""
    r = requests.get(DPC_API + '/findLastProductByType', params={'type': product}, headers=dpc_headers(), timeout=60)
    if r.status_code == 404:
        raise RuntimeError(product + ': no product on the platform (404)')
    r.raise_for_status()
    j = r.json()
    lst = j.get('lastProducts') if isinstance(j, dict) else None
    t = (lst[0].get('time') if lst else None) if isinstance(lst, list) else (j.get('time') if isinstance(j, dict) else None)
    if t is None:
        raise RuntimeError('findLastProductByType answered without a time: ' + str(j)[:200])
    t = int(t)
    return dt.datetime.fromtimestamp(t / 1000.0 if t > 10 ** 11 else t, dt.timezone.utc).replace(second=0, microsecond=0), t


def dpc_download(product, t_raw):
    """One product as the platform serves it: downloadProduct answers a pre-signed S3 link ({bucket, key, url, expiresSeconds}); the file is a GeoTIFF."""
    r = requests.post(DPC_API + '/downloadProduct', json={'productType': product, 'productDate': t_raw}, headers=dpc_headers({'Content-Type': 'application/json'}), timeout=120)
    r.raise_for_status()
    ctype = r.headers.get('content-type', '')
    if 'json' in ctype:
        j = r.json(); url = j.get('url') if isinstance(j, dict) else None
        if not url:
            raise RuntimeError('downloadProduct answered without a link: ' + str(j)[:200])
        r2 = requests.get(url, headers={'User-Agent': UA}, timeout=180)
        r2.raise_for_status()
        return r2.content, r2.headers.get('content-type', ''), str(j.get('key', ''))[:120]
    return r.content, ctype, r.headers.get('content-disposition', '')


def dpc_frame(frame):
    """The GeoTIFF's frame with the platform's custom Transverse Mercator where the file names no EPSG of its own (the SMHI fallback in read_geotiff
    would say SWEREF99 TM): the docs' gdalinfo shows lat_0 42, lon_0 12.5, false easting/northing 0, 1 km pixels from (-600000, 650000)."""
    f = dict(frame)
    if abs(float(f.get('x0', 0.0))) <= 360.0 and abs(float(f.get('xs', 0.0))) < 1.0:   # the CUM products come in plain lon/lat (degrees)
        f.pop('crs', None); f['epsg'] = 4326
    else:                                                                       # VMI, SRI and the rest: metres from (-600000, 650000) — the custom TM; the file's keys name WGS 84 as the datum (2048 = 4326) and a user-defined TM (3075 = 1), which read_geotiff reads as «EPSG 4326»
        f['crs'] = DPC_TM
    return f


def dpc_rate(data, frame, product):
    """The product as mm/h: SRI is rain intensity already; VMI is reflectivity in dBZ (Marshall-Palmer, below 5 dBZ dry). The file's nodata and the
    platform's -9999 are no data."""
    v = data.astype(np.float32)
    nod = frame.get('nodata')
    try:
        nod = float(nod) if nod not in (None, '') else None
    except (TypeError, ValueError):
        nod = None
    bad = ~np.isfinite(v) | (v <= -999.0)
    if nod is not None:
        bad |= np.isclose(v, nod)
    if product == 'VMI':
        r = (np.power(10.0, v / 10.0) / 200.0) ** (1.0 / 1.6)
        r[v < 5.0] = 0.0
    else:
        r = v.copy()
        r[r < 0] = 0.0
    r[bad] = np.nan
    return r.astype(np.float32)


def run_dpc(src, a):
    """The Italian source: the newest mosaic of the Protezione Civile platform (SRI first, VMI when SRI is missing), read as a GeoTIFF, onto the lon/lat grid."""
    d = a.out + '/' + src['id']
    step = src.get('step', a.step)
    last_err = None
    for product in ('SRI', 'VMI'):
        try:
            stamp, t_raw = dpc_last(product)
        except Exception as e:
            last_err = e; log(src['id'], product, 'no last product:', e); continue
        if os.path.exists(d + '/frames/' + stamp.strftime('%Y%m%d%H%M') + '.png'):
            log(src['id'], 'already there', stamp.strftime('%Y%m%d%H%M'), '(' + product + ')'); return 0
        try:
            blob, ctype, disp = dpc_download(product, t_raw)
            log(src['id'], product, stamp.strftime('%Y-%m-%dT%H:%MZ'), len(blob), 'bytes', ctype, disp[:80])
            data, frame = read_geotiff(blob)
            frame = dpc_frame(frame)
            rate = dpc_rate(data, frame, product)
            field, size = reproject_grid(rate, frame, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if product == 'SRI' and convert.coverage_stale(d, stamp):                  # SRI keeps 0.0 where a radar sees dry ground and -9999 beyond the reach; VMI masks everything under 10 dBZ, so its blanks say nothing about the reach
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': DPC_API, 'note': src.get('note', ''), 'quantity': product, 'source_crs': frame.get('crs') or ('EPSG:%s' % frame.get('epsg'))})   # the picture itself is lon/lat (write_frames says crs EPSG:4326); this names the file's own grid
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'product', product, 'epsg', frame.get('epsg')); return 1
        except Exception as e:
            last_err = e; log(src['id'], product, 'failed:', e)
    raise RuntimeError('no picture converted' + (': ' + str(last_err)[:200] if last_err else ''))


def probe_dpc():
    """What Radar-DPC answers: the newest SRI/VMI/SRT1 times (the documented shape), one VMI download without and with the origin header (the docs
    say it is required), the GeoTIFF's frame (the custom Transverse Mercator the docs show), the value histogram; the SRI the same."""
    for product in ('SRI', 'VMI', 'SRT1'):
        try:
            stamp, t_raw = dpc_last(product)
            log(product, 'last', stamp.strftime('%Y-%m-%dT%H:%MZ'), 'raw', t_raw)
        except Exception as e:
            log(product, 'last failed:', e)
    for with_origin in (False, True):
        try:
            h = dpc_headers({'Content-Type': 'application/json'})
            if not with_origin:
                h.pop('Origin'); h.pop('Referer')
            r0 = requests.get(DPC_API + '/findLastProductByType', params={'type': 'VMI'}, headers=h, timeout=60)
            log('findLastProductByType VMI', 'with' if with_origin else 'without', 'origin →', r0.status_code, r0.text[:160])
            if r0.status_code != 200:
                continue
            t_raw = int(r0.json()['lastProducts'][0]['time'])
            r = requests.post(DPC_API + '/downloadProduct', json={'productType': 'VMI', 'productDate': t_raw}, headers=h, timeout=120)
            log('downloadProduct VMI', 'with' if with_origin else 'without', 'origin →', r.status_code, r.headers.get('content-type'), r.text[:200] if 'json' in (r.headers.get('content-type') or '') else len(r.content))
            if r.status_code == 200:
                blob, ctype, key = dpc_download('VMI', t_raw)
                log('  file', key, len(blob), 'bytes', ctype)
                data, frame = read_geotiff(blob)
                log('  geotiff:', data.shape, data.dtype, 'frame', {k: frame[k] for k in ('x0', 'y0', 'xs', 'ys', 'epsg', 'nodata')}, 'ascii', (frame.get('ascii') or '')[:100], 'keys', frame.get('keys'))
                v = data.astype(np.float32); fin = v[np.isfinite(v)]
                log('  values: min %.1f max %.1f; share <= -999: %.3f; share > 5: %.3f; share nan: %.3f' % (float(fin.min()), float(fin.max()), float(np.mean(v <= -999)), float(np.mean(v > 5)), float(np.mean(~np.isfinite(v)))))
                good = fin[fin > -999]
                log('  percentiles (1/10/50/90/99) of the values above -999:', [round(float(q), 2) for q in np.percentile(good, [1, 10, 50, 90, 99])] if good.size else [])
                f2 = dpc_frame(frame); rate = dpc_rate(data, f2, 'VMI')
                field, size = reproject_grid(rate, f2, (6.0, 35.5, 19.0, 47.5), 0.02)
                log('  on the lon/lat grid (0.02°):', size, 'share with data %.3f, share wet %.3f' % (float(np.mean(np.isfinite(field))), float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0), 'crs', f2.get('crs', f2.get('epsg')))
        except Exception as e:
            log('VMI', 'with' if with_origin else 'without', 'origin failed:', e)
    try:
        stamp, t_raw = dpc_last('SRI')
        blob, ctype, key = dpc_download('SRI', t_raw)
        data, frame = read_geotiff(blob)
        v = data.astype(np.float32); fin = v[np.isfinite(v) & (v > -999)]
        log('SRI geotiff:', data.shape, data.dtype, {k: frame[k] for k in ('x0', 'y0', 'xs', 'ys', 'epsg', 'nodata')}, 'valid values min %.2f max %.2f, share > -999 (the reach) %.3f, share >= 0.1 mm/h %.3f' % (float(fin.min()), float(fin.max()), float(np.mean(v > -999)), float(np.mean(v >= 0.1))))
        f2 = dpc_frame(frame); rate = dpc_rate(data, f2, 'SRI')
        field, size = reproject_grid(rate, f2, (6.0, 35.5, 19.0, 47.5), 0.02)
        fin2 = np.isfinite(field)
        rows = np.where(fin2.any(axis=1))[0]; cols = np.where(fin2.any(axis=0))[0]
        log('  SRI on the lon/lat grid (0.02°):', size, 'share with data %.3f, share wet %.3f' % (float(np.mean(fin2)), float(np.nanmean(field >= 0.1)) if fin2.any() else 0.0), 'crs', f2.get('crs'),
            'data rows %d–%d of %d, cols %d–%d of %d' % (rows.min(), rows.max(), size[1], cols.min(), cols.max(), size[0]) if rows.size else 'no data cell')
        # the reach as a rough picture: every tenth cell, # = data, . = none (south at the bottom)
        for r in range(0, field.shape[0], 40):
            log('  ', ''.join('#' if fin2[r, c] else '.' for c in range(0, field.shape[1], 12)))
    except Exception as e:
        log('SRI failed:', e)
    return 0


def probe_chmi():
    """What ČHMÚ's portal offers: the licence lines of its front page, the newest MAX_Z files, one file's HDF5 layout and its where/what attributes."""
    try:
        html = get(CHMI_ROOT, headers={'User-Agent': UA, 'Accept': 'text/html, */*'}).text
        text = re.sub(r'<[^>]+>', ' ', html); text = re.sub(r'\s+', ' ', text)
        hits = [m.group(0) for m in re.finditer(r'.{0,120}(?:licen|Licen|LICEN|Creative Commons|CC[ -]BY|CC0).{0,160}', text)]
        log('chmi portal: licence lines:', hits[:4] if hits else 'none found on the front page')
    except Exception as e:
        log('chmi portal front page failed:', e)
    items = chmi_files(3)
    log('chmi files:', [(t.strftime('%Y-%m-%dT%H:%MZ'), u.rsplit('/', 1)[-1]) for t, u in items])
    if not items:
        return 1
    import h5py
    blob = get(items[-1][1]).content
    log('chmi file bytes', len(blob))
    with h5py.File(io.BytesIO(blob), 'r') as f:
        def walk(name, obj):
            attrs = {k: (v.decode() if isinstance(v, bytes) else (v.tolist() if hasattr(v, 'tolist') else v)) for k, v in obj.attrs.items()}
            log('  ', name, getattr(obj, 'shape', ''), json.dumps(attrs, default=str)[:400])
        f.visititems(walk)
        log('  root attrs', json.dumps({k: (v.decode() if isinstance(v, bytes) else str(v)) for k, v in f.attrs.items()})[:400])
    with h5py.File(io.BytesIO(blob), 'r') as f:                                   # the raw values: how many are above undetect — a dry picture or a wrong field?
        raw = f['dataset1/data1/data'][...]
        vals, cnts = np.unique(raw, return_counts=True)
        log('chmi raw: dtype', raw.dtype, 'min', int(raw.min()), 'max', int(raw.max()), 'distinct', len(vals), 'share raw>0', round(float(np.mean(raw > 0)), 4), 'top values', [(int(v), int(c)) for v, c in sorted(zip(vals, cnts), key=lambda x: -x[1])[:8]])
        for k in list(f.keys()):
            log('  group', k, list(f[k].keys()) if hasattr(f[k], 'keys') else '')
    rate, grid, scale, quantity = convert.read_odim(blob)
    rate = odim_rate(rate, quantity)
    log('chmi read: quantity', quantity, 'scale', scale, 'grid', {k: v for k, v in grid.items() if k != 'proj'}, 'proj', grid['proj'][:120])
    log('chmi rate: finite share', round(float(np.mean(np.isfinite(rate))), 3), 'wet share', round(float(np.nanmean(rate >= 0.1)), 4) if np.isfinite(rate).any() else 'n/a',
        'dBZ max', round(float(np.nanmax(raw.astype(np.float32) * 0.5 - 32.0)), 1))
    # the portal's own PNG of the same minute: how much of it is painted (a cross-check of «dry»)
    try:
        stamp = items[-1][0]
        png = CHMI.replace('/hdf5/', '/png/') + 'pacz2gmaps3.z_max3d.%s.%s.0.png' % (stamp.strftime('%Y%m%d'), stamp.strftime('%H%M'))
        r = requests.get(png, headers={'User-Agent': UA}, timeout=60)
        log('chmi png', png.rsplit('/', 1)[-1], r.status_code, len(r.content), 'bytes')
        if r.ok:
            im = Image.open(io.BytesIO(r.content)).convert('RGBA'); arr = np.asarray(im)
            log('chmi png: size', im.size, 'painted share', round(float(np.mean(arr[..., 3] > 0)), 4), 'distinct colours', len(np.unique(arr.reshape(-1, 4), axis=0)))
    except Exception as e:
        log('chmi png failed:', e)
    return 0


def knmi_read(blob):
    """KNMI's radar HDF5 (not ODIM): image1/image_data (uint8, 255 = no data), the calibration formula GEO = a * PV + b (dBZ), the corners and the
    polar-stereographic proj4 under geographic/ — → (rate mm/h with NaN outside, frame for reproject_grid)."""
    import h5py
    with h5py.File(io.BytesIO(blob), 'r') as f:
        raw = f['image1/image_data'][...]
        cal = dict(f['image1/calibration'].attrs) if 'image1/calibration' in f else {}
        geo = dict(f['geographic'].attrs) if 'geographic' in f else {}
        mp = dict(f['geographic/map_projection'].attrs) if 'geographic/map_projection' in f else {}
    def s(v):
        v = v[0] if isinstance(v, np.ndarray) and v.size == 1 else v
        return v.decode('utf-8', 'replace') if isinstance(v, bytes) else v
    formula = str(s(cal.get('calibration_formulas', 'GEO = 0.500000 * PV + -31.500000')))
    m = re.search(r'GEO\s*=\s*([-\d.]+)\s*\*\s*PV\s*\+\s*([-\d.]+)', formula)
    a_, b_ = (float(m.group(1)), float(m.group(2))) if m else (0.5, -31.5)
    # the codes (the keyed runs of 7 Nov 22:20/22:27): KNMI writes calibration_missing_data = 0 and calibration_out_of_image = 255 — 0 is the dry
    # pixel inside the radars' reach (no echo), 255 the pixel beyond it; the first reader took 0 as the only no-data code and painted every 255
    # as 95.5 dBZ, a deluge over the whole square. Beyond reach → NaN (the coverage edge comes from it); 0 → dry.
    nod = {255}
    v = cal.get('calibration_out_of_image')
    if v is not None:
        try: nod.add(int(np.ravel(v)[0]))
        except (TypeError, ValueError): pass
    v = cal.get('calibration_missing_data')
    if v is not None:
        try:
            mv = int(np.ravel(v)[0])
            if mv != 0: nod.add(mv)
        except (TypeError, ValueError): pass
    dbz = raw.astype(np.float32) * a_ + b_
    rate = odim_rate(dbz, 'DBZH')
    rate[np.isin(raw, list(nod))] = np.nan
    rate[dbz > 90.0] = np.nan                                                                              # no radar sees 90 dBZ — a code, not rain
    vals, counts = np.unique(raw, return_counts=True)
    top = sorted(zip(counts.tolist(), vals.tolist()), reverse=True)[:5]
    info_vals = {'nodata_codes': sorted(nod), 'top_values': [(int(v), int(c)) for c, v in top], 'wet_px': int(np.sum(np.isfinite(rate) & (rate >= 0.1))), 'nan_px': int(np.sum(~np.isfinite(rate)))}
    proj4 = str(s(mp.get('projection_proj4_params', '+proj=stere +lat_0=90 +lon_0=0 +lat_ts=60 +a=6378.14 +b=6356.75 +x_0=0 y_0=0'))).replace('+a=6378.14 ', '+a=6378140 ').replace('+b=6356.75 ', '+b=6356750 ')
    corners = np.ravel(geo.get('geo_product_corners', [])).astype(float)
    px = float(np.ravel(geo.get('geo_pixel_size_x', [1.0]))[0]); py = float(np.ravel(geo.get('geo_pixel_size_y', [-1.0]))[0])
    unit = 1000.0 if abs(px) < 50 else 1.0                                                            # KNMI gives the pixel size in km
    tr = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(proj4), always_xy=True)
    if corners.size >= 8:
        xs_, ys_ = tr.transform(corners[0::2], corners[1::2])
        x0, y0 = float(np.min(xs_)), float(np.max(ys_))                                               # the north-west corner
    else:
        x0 = float(np.ravel(geo.get('geo_column_offset', [0]))[0]) * px * unit; y0 = -float(np.ravel(geo.get('geo_row_offset', [0]))[0]) * abs(py) * unit
    frame = {'x0': x0, 'y0': y0, 'xs': abs(px) * unit, 'ys': abs(py) * unit, 'crs': proj4}
    return rate, frame, {'formula': formula, 'shape': raw.shape, 'proj4': proj4, 'corners': corners[:8].tolist(), 'cal': {k: (np.ravel(v).tolist()[:2] if isinstance(v, np.ndarray) else str(v)[:40]) for k, v in cal.items()}, **info_vals}


def knmi_files(key, n=12):
    r = requests.get(KNMI, params={'maxKeys': max(1, n), 'orderBy': 'created', 'sorting': 'desc'}, headers={'Authorization': key, 'User-Agent': UA}, timeout=60)
    r.raise_for_status()
    out = []
    for f in (r.json().get('files') or []):
        name = str(f.get('filename', ''))
        m = re.search(r'_(\d{12})\.h5$', name)
        if m:
            t = m.group(1)
            out.append((parse_time(t[:4] + '-' + t[4:6] + '-' + t[6:8] + 'T' + t[8:10] + ':' + t[10:12] + 'Z'), name))
    return sorted(out)[-n:]


def run_knmi(src, a):
    key = os.environ.get(src['secret'], '').strip()
    if not key:
        log(src['id'], 'skipped — no secret', src['secret']); return 0
    d = a.out + '/' + src['id']; step = src.get('step', a.step)
    files = knmi_files(key, max(1, a.backfill))
    if not files:
        raise RuntimeError('no file listed')
    try:                                                                                                 # the frames painted as a deluge over half the domain (the no-data square of 7 Nov 22:20) are redone, not kept for an hour:
        fj = json.load(open(d + '/frames.json')) if os.path.exists(d + '/frames.json') else {}          # frames.json carries the newest frame's wet share; a bad one marks the whole set of its reader
        if float(fj.get('wet_share', 0) or 0) > 0.5 or os.environ.get('KNMI_REDO', '') == '1':
            n = 0
            for f in os.listdir(d + '/frames') if os.path.isdir(d + '/frames') else []:
                if re.match(r'^\d{12}\.png$', f): os.remove(d + '/frames/' + f); n += 1
            log(src['id'], 'redone — the frames were a deluge (wet share %s): %d dropped' % (fj.get('wet_share'), n))
    except Exception as e:
        log(src['id'], 'frames.json not read:', e)
    todo = [(t, nm) for t, nm in files if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        log(src['id'], 'already there', files[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0
    for stamp, name in todo:
        try:
            u = requests.get(KNMI + '/' + name + '/url', headers={'Authorization': key, 'User-Agent': UA}, timeout=60); u.raise_for_status()
            blob = requests.get(u.json()['temporaryDownloadUrl'], headers={'User-Agent': UA}, timeout=120).content
            rate, frame, info = knmi_read(blob)
            if got == 0:
                log(src['id'], 'file', name, len(blob), 'bytes', info, 'frame', {k: (round(v, 1) if isinstance(v, float) else v) for k, v in frame.items()})
            field, size = reproject_grid(rate, frame, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': KNMI + '/' + name, 'note': src.get('note', '')})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'nodata share', round(float(np.mean(~np.isfinite(field))), 3)); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def mf_app_id(raw):
    """The application id as the portal wants it: the base64 of key:secret — whatever shape the secret was pasted in (the first keyed run,
    7 Nov 21:20: the gateway answered a malformed Basic value with a 200 «Request Rejected» page). Quotes, spaces, a «Basic » or
    «Authorization: Basic » prefix and a trailing newline are dropped; «key:secret» in the clear is encoded. Returns (app_id, note)."""
    import base64, binascii
    v = (raw or '').strip().strip('"\'').strip()
    v = re.sub(r'^(?i:authorization\s*:\s*)?(?i:basic)\s+', '', v).strip().strip('"\'')
    v = re.sub(r'\s+', '', v)
    if ':' in v and not re.fullmatch(r'[A-Za-z0-9+/=]+', v):
        return base64.b64encode(v.encode()).decode(), 'key:secret in the clear — encoded'
    try:
        dec = base64.b64decode(v + '=' * (-len(v) % 4), validate=True).decode('utf-8', 'replace')
        if ':' not in dec:
            return v, 'base64 that holds no key:secret pair — the portal will reject it; is it the token instead of the application id?'
        return v, ''
    except (binascii.Error, ValueError):
        return v, 'not base64 — the application id is the string after «Basic» in the portal\'s curl box'


def mf_token(app_id):
    app_id, note = mf_app_id(app_id)
    if note:
        log('fr app id:', note, '(%d chars)' % len(app_id))
    r = requests.post(MF_TOKEN, data={'grant_type': 'client_credentials'}, headers={'Authorization': 'Basic ' + app_id, 'User-Agent': UA}, timeout=60)
    ct = r.headers.get('content-type', '')
    try:
        j = r.json()
    except ValueError:
        body = re.sub(r'<[^>]+>', ' ', r.text[:300]); body = re.sub(r'\s+', ' ', body).strip()[:160]
        raise RuntimeError('the token endpoint answered %d %s, no JSON: «%s» — a malformed application id gets the gateway\'s «Request Rejected» page; the secret must be the string after «Basic» in the portal\'s curl (base64 of key:secret), nothing else' % (r.status_code, ct, body))
    if r.status_code != 200 or not j.get('access_token'):
        raise RuntimeError('the token endpoint answered %d: %s %s' % (r.status_code, j.get('error', ''), j.get('error_description', '')))
    return str(j.get('access_token', ''))


def archive_members(content):
    """The paquet's members whatever the container (7 Nov 22:37: the first 200 came as application/gzip, 11.8 MB — not the zip the portal's
    page promised): zip, tar, tar.gz, or one gzipped file — opened by the bytes' magic, never by the header. Returns (kind, [(name, bytes)])."""
    import zipfile, tarfile, gzip
    def kind_of(b):
        if b[:4] == b'PK\x03\x04': return 'zip'
        if b[:2] == b'\x1f\x8b': return 'gzip'
        if len(b) > 262 and b[257:262] == b'ustar': return 'tar'
        if b[:4] == b'\x89HDF': return 'hdf5'
        if b[:4] == b'BUFR': return 'bufr'
        if b[:4] in (b'II*\x00', b'MM\x00*'): return 'tiff'
        if b[:4] == b'GRIB': return 'grib'
        return 'unknown'
    k = kind_of(content)
    if k == 'zip':
        z = zipfile.ZipFile(io.BytesIO(content)); return 'zip', [(n, z.read(n)) for n in z.namelist() if not n.endswith('/')]
    if k == 'tar':
        t = tarfile.open(fileobj=io.BytesIO(content), mode='r:'); return 'tar', [(m.name, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
    if k == 'gzip':
        try:
            t = tarfile.open(fileobj=io.BytesIO(content), mode='r:gz')
            mem = [(m.name, t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
            if mem: return 'tar.gz', mem
        except tarfile.TarError:
            pass
        inner = gzip.decompress(content)
        ki = kind_of(inner)
        if ki in ('zip', 'tar'):
            kk, mem = archive_members(inner); return 'gzip+' + kk, mem
        return 'gzip', [('paquet.' + ki, inner)]
    return k, [('paquet.' + k, content)]


def run_meteofrance(src, a):
    """Météo-France's Paquet Radar: the last quarter hour's mosaics in one archive — the members are logged on every run until the reader knows
    them (the first pass with a key tells the format); ODIM HDF5 and GeoTIFF members are tried, by their bytes rather than their names."""
    app = os.environ.get(src['secret'], '').strip()
    if not app:
        log(src['id'], 'skipped — no secret', src['secret']); return 0
    d = a.out + '/' + src['id']; step = src.get('step', a.step)
    tok = mf_token(app)
    if not tok:
        raise RuntimeError('no token from the portal')
    r = requests.get(MF_PAQUET, headers={'Authorization': 'Bearer ' + tok, 'Accept': '*/*', 'User-Agent': UA}, timeout=180)
    log(src['id'], 'paquet', r.status_code, r.headers.get('content-type'), len(r.content), 'bytes')
    if r.status_code in (401, 403):                                                                      # the token is good, the API is not open to the application (22:27: 403) — the portal's subscription is missing
        body = re.sub(r'\s+', ' ', r.text[:200])
        raise RuntimeError('%d from the Paquet Radar API: %s — the application has no subscription to «Paquet Radar» (DPPaquetRadar) in the portal: Mes applications → DefaultApplication' % (r.status_code, body))
    r.raise_for_status()
    kind, members = archive_members(r.content)
    try:                                                                                               # frames written before 7 Nov 22:45 held mm per 5 min as mm/h (12× too low): once, they go
        fj = d + '/frames.json'
        if os.path.exists(fj) and 'acc' not in json.load(open(fj)):
            import shutil; shutil.rmtree(d + '/frames', ignore_errors=True); os.remove(fj); log(src['id'], 'frames of the old scale dropped')
    except Exception as e:
        log(src['id'], 'old frames:', e)
    log(src['id'], 'archive', kind, 'members', len(members), [(n, len(b)) for n, b in members if n.startswith(src.get('member', 'T_IPRN20_'))])
    got = 0
    def magic(b):
        return 'hdf5' if b[:4] == b'\x89HDF' else 'tiff' if b[:4] in (b'II*\x00', b'MM\x00*') else 'bufr' if b[:4] == b'BUFR' else 'grib' if b[:4] == b'GRIB' else 'gzip' if b[:2] == b'\x1f\x8b' else '?'
    want = src.get('member', 'T_IPRN20_')                                                              # the metropolitan 500 m lame d'eau (ODIM HDF5, ACRR per 5 min); IPRN21/IMFR27 are BUFR, IPRE… the overseas zones
    others = [n for n, b in members if want and not n.startswith(want)]
    if others:
        log(src['id'], 'members left aside:', len(others), sorted(set(re.sub(r'_\d{14}.*$', '', n) for n in others)))
    seen = 0
    for name, blob in members:
        if want and not name.startswith(want):
            continue
        low = name.lower()
        try:
            if magic(blob) == 'gzip':                                                                  # a member gzipped on its own
                import gzip; blob = gzip.decompress(blob); low = re.sub(r'\.gz$', '', low)
            mg = magic(blob)
            m = re.search(r'(\d{8})[T_]?(\d{4})', name); stamp = parse_time(m.group(1)[:4] + '-' + m.group(1)[4:6] + '-' + m.group(1)[6:8] + 'T' + m.group(2)[:2] + ':' + m.group(2)[2:] + 'Z') if m else dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
            if mg == 'hdf5' or low.endswith(('.h5', '.hdf5', '.hdf')):
                rate, grid, scale, quantity = convert.read_odim(blob)
                log('  member', name, len(blob), 'bytes hdf5', quantity, getattr(rate, 'shape', None), {k: grid.get(k) for k in ('xsize', 'ysize', 'xscale', 'yscale') if isinstance(grid, dict) and k in grid})
                rate = odim_rate(rate, quantity, src.get('step_min', 5))
                field, _, size = convert.reproject(rate, grid, src['bbox'], step)
            elif mg == 'tiff' or low.endswith(('.tif', '.tiff')):
                data, frame = read_geotiff(blob)
                rate = data.astype(np.float32); rate[rate < 0] = np.nan
                field, size = reproject_grid(rate, frame, src['bbox'], step)
            else:
                log('  member', name, len(blob), 'bytes', mg, repr(blob[:24]), '— not read yet (format unknown to the reader)'); continue
            if os.path.exists(d + '/frames/' + stamp.strftime('%Y%m%d%H%M') + '.png'):
                seen += 1; continue
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': MF_PAQUET, 'note': src.get('note', ''), 'member': name, 'acc': 'ACRR mm per %d min, shown as mm/h' % int(src.get('step_min', 5))})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'from', name); got += 1
        except Exception as e:
            log('  member', name, 'failed:', e)
    if not got and seen:
        log(src['id'], 'already there —', seen, 'members converted before'); return 0                 # the quarter hour's three steps were read by an earlier pass (8 Nov 00:30): no failure
    if not got:
        raise RuntimeError('no mosaic read from the archive — the members are in the log')
    return got


def run_aemet(src, a):
    """AEMET's national radar composite (OpenData, api/red/radar/nacional): a two-step call — the API answers with a short-lived link, the link
    with the picture. The first passes (probe_only) keep the raw picture at es/_probe.png with its size and mode in the log, so the frame can
    be calibrated by hand against the coastline; the reader for the colours and the frame follows."""
    key = os.environ.get(src['secret'], '').strip()
    if not key:
        log(src['id'], 'skipped — no secret', src['secret']); return 0
    d = a.out + '/' + src['id']
    j = None; last = ''
    for url in (AEMET, AEMET, AEMET, AEMET.replace('/nacional', '/regional/pm')):                         # the OpenData gateway answers «estado 404 — Error al obtener los datos» now and then (7 Nov 22:57): three tries, then one regional radar (Palma) to tell a key problem from a product problem
        r = requests.get(url, headers={'api_key': key, 'Accept': 'application/json', 'User-Agent': UA}, params={'api_key': key}, timeout=60)
        log(src['id'], 'api', url.rsplit('/radar/', 1)[-1], r.status_code, r.headers.get('content-type'), len(r.content), 'bytes', re.sub(r'\s+', ' ', r.text[:160]))
        try:
            jj = r.json()
        except ValueError:
            last = 'the AEMET API answered %d without JSON: %s' % (r.status_code, re.sub(r'\s+', ' ', r.text[:160])); time.sleep(20); continue
        if r.status_code == 200 and int(jj.get('estado', 0) or 0) == 200 and jj.get('datos'):
            j = jj; break
        last = 'the AEMET API answered %d: estado %s — %s' % (r.status_code, jj.get('estado'), jj.get('descripcion', ''))
        time.sleep(20)
    if j is None:
        raise RuntimeError(last)
    r2 = requests.get(j['datos'], headers={'User-Agent': UA}, timeout=120)
    log(src['id'], 'datos', r2.status_code, r2.headers.get('content-type'), len(r2.content), 'bytes', 'last-modified', r2.headers.get('last-modified'))
    r2.raise_for_status()
    blob = r2.content
    if j.get('metadatos'):
        try:
            r3 = requests.get(j['metadatos'], headers={'User-Agent': UA}, timeout=60); log(src['id'], 'metadatos', r3.status_code, re.sub(r'\s+', ' ', r3.text[:700]))
        except Exception as e:
            log(src['id'], 'metadatos failed:', e)
    im = Image.open(io.BytesIO(blob))
    log(src['id'], 'picture', im.format, im.mode, im.size, 'frames', getattr(im, 'n_frames', 1), 'info', {k: (v if isinstance(v, (int, float, str)) else str(type(v))) for k, v in list(im.info.items())[:8]})
    os.makedirs(d, exist_ok=True)
    im.convert('RGBA').save(d + '/_probe.png')
    open(d + '/_probe.' + (im.format or 'bin').lower(), 'wb').write(blob)
    if src.get('probe_only'):
        json.dump({'ok': False, 'probe': True, 'size': list(im.size), 'mode': im.mode, 'format': im.format, 'at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'note': 'the raw picture kept for the calibration; no frames yet'}, open(d + '/status.json', 'w'), indent=1)
        log(src['id'], 'probe kept — the reader for the colours and the frame follows')
        return 0
    raise RuntimeError('the AEMET reader is not written yet')


IMO_PAGE = 'https://en.vedur.is/weather/observations/radar/'
IMO_LEGEND = [((75, 75, 128), -5), ((22, 22, 128), 0), ((10, 65, 191), 5), ((15, 180, 240), 10), ((28, 231, 156), 15), ((85, 170, 0), 20), ((169, 212, 0), 25),
              ((255, 255, 0), 30), ((254, 101, 0), 35), ((212, 51, 0), 40), ((170, 0, 0), 45)]                    # the picture's legend: dBZ classes of 5 (the -10 box is a light grey — no rain)
IMO_CAL = {'P': (230.4, -1766.0), 'n': 0.9016442926106257, 'F': 7565.154382629653, 'lon0': -20.0187245540686}  # the picture's Lambert conformal conic, fitted from its graticule (8 Nov 00:30): pole pixel P, r = F·tan(π/4−φ/2)^n, θ = n·(λ−λ0)


def imo_pictures():
    """The radar_isl pictures the IMO radar page names (every 5 min, two hours), oldest first: [(stamp, url)]. The page is script-built; the
    addresses stand in its source as https://en.vedur.is/photos/radar_isl/YYMMDD_HHMM.png (the time is UTC — Iceland keeps UTC all year)."""
    r = requests.get(IMO_PAGE, headers={'User-Agent': UA}, timeout=60); r.raise_for_status()
    out = {}
    hits = re.findall(r'radar_isl[/\\]+(\d{6})_(\d{4})\.png', r.text)
    log('is page', r.status_code, len(r.text), 'chars,', len(hits), 'radar_isl names', hits[:2], hits[-1:])
    for d6, t4 in hits:
        try:
            out[parse_time('20' + d6[:2] + '-' + d6[2:4] + '-' + d6[4:6] + 'T' + t4[:2] + ':' + t4[2:] + 'Z')] = 'https://en.vedur.is/photos/radar_isl/%s_%s.png' % (d6, t4)
        except Exception as e:
            log('is time', d6, t4, 'failed:', e)
    return [(t, out[t]) for t in sorted(out)]


def imo_read(blob):
    """The IMO composite picture as a rain field: (rate mm/h — NaN beyond the radars' reach, 0 where they see dry air —, pixel frame). Rain by the
    nearest legend colour (the picture is drawn with smooth shades), dBZ → mm/h by Marshall–Palmer, under 5 dBZ dry; the grey outside the circles
    (185) is no data; land (222), sea and glaciers (254), the coast (black) and the graticule (grey) are dry."""
    im = Image.open(io.BytesIO(blob)).convert('RGB'); a = np.array(im).astype(np.int16); H, W = a.shape[:2]
    if (W, H) != (540, 383):
        raise RuntimeError('the IMO picture changed its size: %dx%d (the calibration is for 540x383)' % (W, H))
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    sat = (a.max(axis=2) - a.min(axis=2)) > 40
    area = np.zeros((H, W), bool); area[12:350, 3:537] = True                                        # the map without the title row, the legend and the frame
    cols = np.array([c for c, v in IMO_LEGEND], dtype=np.int16); vals = np.array([v for c, v in IMO_LEGEND], dtype=np.float32)
    dbz = np.full((H, W), np.nan, dtype=np.float32)
    cand = sat & area
    if cand.any():
        px = a[cand].astype(np.int32); dist = ((px[:, None, :] - cols[None, :, :].astype(np.int32)) ** 2).sum(axis=2); near = dist.argmin(axis=1); good = dist[np.arange(len(px)), near] < 70 ** 2   # int32: the squares overflow int16
        yy, xx = np.where(cand); dbz[yy[good], xx[good]] = vals[near[good]] + 2.5
    rate = (np.power(10.0, dbz / 10.0) / 200.0) ** (1.0 / 1.6)
    rate[dbz < 5] = 0.0
    outside = (r == 185) & (g == 185) & (b == 185)                                                   # beyond the radars' reach
    dry = ~np.isfinite(dbz) & ~outside & area
    rate[dry] = 0.0
    rate[outside | ~area] = np.nan
    return rate.astype(np.float32), {'w': W, 'h': H}


def imo_project(lon, lat):
    """lon/lat → the picture's pixel (x, y) by its fitted Lambert conformal conic."""
    c = IMO_CAL
    rr = c['F'] * np.power(np.tan(np.pi / 4 - np.radians(lat) / 2), c['n']); th = np.radians(c['n'] * (lon - c['lon0']))
    return c['P'][0] + rr * np.sin(th), c['P'][1] + rr * np.cos(th)


def imo_field(rate, bbox, step):
    """The rain field on the lon/lat grid of the source's box: every cell samples the picture pixel under its centre (the pixels are ≈ 1.5 km,
    the cells 0.02°); a cell outside the picture or beyond the reach is NaN."""
    W, S, E, N = bbox
    lons = np.arange(W + step / 2, E, step); lats = np.arange(N - step / 2, S, -step)
    LO, LA = np.meshgrid(lons, lats); X, Y = imo_project(LO, LA)
    xi = np.round(X).astype(int); yi = np.round(Y).astype(int); H, Wd = rate.shape
    ok = (xi >= 0) & (xi < Wd) & (yi >= 0) & (yi < H)
    field = np.full(LO.shape, np.nan, dtype=np.float32); field[ok] = rate[yi[ok], xi[ok]]
    return field, (len(lons), len(lats))


def run_imo(src, a):
    """Iceland (the owner's «Ja», 8 Nov 00:17): the IMO composite of the three radars (Keflavík, Bjólfur, Skaga) as the radar page publishes it —
    a picture every 5 minutes, read by its legend and placed by its graticule (imo_read, imo_field); the coverage edge from the picture's grey
    beyond the reach. IMO's conditions of use allow it with the attribution and the date (the frames carry both)."""
    d = a.out + '/' + src['id']; step = src.get('step', a.step); os.makedirs(d, exist_ok=True)
    pics = imo_pictures()
    if not pics:
        raise RuntimeError('the IMO radar page named no picture')
    todo = [(t, u) for t, u in pics if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')][-max(1, a.backfill):]
    if not todo:
        log(src['id'], 'already there', pics[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0
    for stamp, url in todo:
        try:
            r = requests.get(url, headers={'User-Agent': UA, 'Referer': IMO_PAGE}, timeout=60); r.raise_for_status()
            rate, frame = imo_read(r.content)
            field, size = imo_field(rate, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': url, 'note': src.get('note', ''), 'attribution_date': stamp.strftime('%Y-%m-%d')})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'nodata share', round(float(np.mean(~np.isfinite(field))), 3)); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def nordic_files(n=12):
    """The newest n files of the Nordic composite in the THREDDS folder latest/ (two days of 5-min files), oldest first: [(stamp, path)]."""
    r = get(NORDIC_CAT)
    out = []
    for f in sorted(set(re.findall(r'dataset=([^"&]+\.nc)', r.text))):
        m = re.search(r'\.(\d{8})T(\d{4})\d{0,2}Z\.nc$', f)
        if m:
            d, t = m.group(1), m.group(2)
            try:
                out.append((parse_time(d[:4] + '-' + d[4:6] + '-' + d[6:8] + 'T' + t[:2] + ':' + t[2:] + 'Z'), f))
            except Exception:
                pass
    return sorted(out)[-n:]


def nordic_read(path, stride=2):
    """One composite file through OPeNDAP, every stride-th cell: (rate mm/h with NaN where no radar sees, frame with the proj4 string, info)."""
    import netCDF4
    ds = netCDF4.Dataset(THREDDS + '/dodsC/' + path)
    try:
        xc = np.array(ds['Xc'][::stride], dtype=np.float64); yc = np.array(ds['Yc'][::stride], dtype=np.float64)
        v = ds['lwe_precipitation_rate']
        units = str(getattr(v, 'units', '')); fill = getattr(v, '_FillValue', None)
        rate = v[0, ::stride, ::stride]
        rate = np.array(rate.filled(np.nan) if np.ma.isMaskedArray(rate) else rate, dtype=np.float32)
        nod = None
        if 'is_nodata' in ds.variables:
            nd = ds['is_nodata'][0, ::stride, ::stride]
            nod = np.array(nd.filled(0) if np.ma.isMaskedArray(nd) else nd)
        pm = ds['projection_lambert'] if 'projection_lambert' in ds.variables else None
        proj4 = str(getattr(pm, 'proj4', '')) if pm is not None else ''
        nodes = str(getattr(ds['mosaic_info'], 'nodes', ''))[:200] if 'mosaic_info' in ds.variables else ''
    finally:
        ds.close()
    if not proj4:
        proj4 = '+proj=lcc +lat_0=63 +lon_0=15 +lat_1=63 +lat_2=63 +no_defs +R=6.371e+06'
    if fill is not None:
        rate[rate == np.float32(fill)] = np.nan
    if nod is not None:
        rate[nod != 0] = np.nan
    if 's-1' in units or 's^-1' in units:                               # kg m-2 s-1 → mm/h
        rate = rate * 3600.0
    rate[rate < 0] = np.nan
    if yc[0] < yc[-1]:                                                  # south first in the file: row 0 must be north
        rate = rate[::-1]; yc = yc[::-1]
    xs = float(abs(xc[1] - xc[0])); ys = float(abs(yc[0] - yc[1]))
    frame = {'x0': float(xc[0]) - xs / 2, 'y0': float(yc[0]) + ys / 2, 'xs': xs, 'ys': ys, 'crs': proj4}
    return rate, frame, {'units': units, 'nodes': nodes, 'shape': rate.shape, 'nodata_share': round(float(np.mean(~np.isfinite(rate))), 3)}


def run_nordic(src, a):
    """MET Norway's Nordic composite: the newest files not yet converted (--backfill), oldest first; a file that fails is logged, the rest go on."""
    d = a.out + '/' + src['id']
    step = src.get('step', a.step)
    todo = [(t, f) for t, f in nordic_files(max(1, a.backfill)) if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
    if not todo:
        lst = nordic_files(1)
        if not lst:
            raise RuntimeError('no file in the THREDDS folder')
        log(src['id'], 'already there', lst[-1][0].strftime('%Y%m%d%H%M')); return 0
    got = 0
    for stamp, path in todo:
        try:
            rate, frame, info = nordic_read(path)
            if got == 0:
                log(src['id'], 'file', path.split('/')[-1], info, 'frame', {k: (round(v, 1) if isinstance(v, float) else v) for k, v in frame.items()})
            field, size = reproject_grid(rate, frame, src['bbox'], step)
            field[np.isfinite(field) & (field < 0.1)] = 0.0
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            if convert.coverage_stale(d, stamp):
                convert.write_coverage(d, np.isfinite(field), src['bbox'], step, src['source'])           # the reach's edge from is_nodata (6 Nov, late)
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': step, 'url': THREDDS + '/dodsC/' + path, 'note': src.get('note', ''), 'radars': info.get('nodes', '')})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n, 'nodata share', info['nodata_share']); got += 1
        except Exception as e:
            log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
    if not got:
        raise RuntimeError('no picture converted')
    return got


def probe_norway():
    """MET Norway: the api.met.no radar picture of an area and the area's metadata page (projection, extent) — do they fit?"""
    for area in ('nordic', 'norway', 'southern_norway'):
        for typ in ('reflectivity', '5level_reflectivity'):
            try:
                r = requests.get('https://api.met.no/weatherapi/radar/2.0/', params={'area': area, 'type': typ, 'content': 'image'}, headers={'User-Agent': UA}, timeout=60)
                ok = r.status_code == 200 and r.content[:4] == b'\x89PNG'
                size = Image.open(io.BytesIO(r.content)).size if ok else None
                log('api.met.no', area, typ, r.status_code, r.headers.get('content-type'), 'size', size, 'last-modified', r.headers.get('last-modified'), 'bytes', len(r.content))
                if ok:
                    im = Image.open(io.BytesIO(r.content)).convert('RGBA'); a = np.array(im)
                    log('   alpha share', round(float(np.mean(a[..., 3] > 0)), 3), 'distinct colours', len(np.unique(a.reshape(-1, 4), axis=0)))
            except Exception as e:
                log('api.met.no', area, typ, 'failed:', e)
        for prod in ('reflectivity', '5level_reflectivity'):
            try:
                r = requests.get('https://radarapi.met.no/display-area-metadata/%s/%s' % (area, prod), headers={'User-Agent': UA}, timeout=60)
                txt = re.sub(r'<[^>]+>', ' ', r.text); txt = re.sub(r'\s+', ' ', txt)
                m = re.search(r'\+proj=[^"<]{0,200}', r.text)
                log('radarapi', area, prod, r.status_code, 'proj', m.group(0) if m else None, 'text', txt[:600])
            except Exception as e:
                log('radarapi', area, prod, 'failed:', e)
    return 0


def probe_dmi():
    """DMI's radar data API (6 Nov, late): the composite items of the last two hours and one file's HDF5 tree with every attribute."""
    import h5py
    now = dt.datetime.now(dt.timezone.utc)
    r = get(DMI + '/collections/composite/items', params={'limit': 5, 'datetime': (now - dt.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ') + '/' + now.strftime('%Y-%m-%dT%H:%M:%SZ')})
    j = r.json(); feats = j.get('features') or []
    log('dmi items', len(feats), 'keys', list(j.keys()))
    for ft in feats[:2]:
        log('  feature', json.dumps({k: (v if k != 'geometry' else '…') for k, v in ft.items()})[:900])
    items = dmi_items(1)
    if not items:
        return 0
    blob = get(items[-1][1]).content
    log('file', items[-1][0].isoformat(), len(blob), 'bytes', blob[:8])
    def s(v):
        if isinstance(v, bytes): return v.decode('utf-8', 'replace')
        if isinstance(v, np.ndarray): return v.tolist() if v.size < 12 else ('array ' + str(v.shape))
        if isinstance(v, np.generic): return v.item()
        return v
    with h5py.File(io.BytesIO(blob), 'r') as f:
        def walk(name, obj):
            at = {k: s(v) for k, v in obj.attrs.items()}
            extra = ''
            if isinstance(obj, h5py.Dataset):
                extra = ' dataset %s %s' % (obj.shape, obj.dtype)
                if obj.ndim == 2 and obj.size < 5e6:
                    arr = obj[...]; extra += ' min %s max %s' % (np.nanmin(arr), np.nanmax(arr))
            log('   ', name, extra, json.dumps(at)[:600])
        walk('/', f); f.visititems(walk)
    return 0


def probe_thredds():
    """MET Norway's THREDDS server (6 Nov, late): are the Nordic radar composites there as data — netCDF grids, no login — rather than
    the rendered pictures of api.met.no? The catalogues named by the esd R package and the WeaMyL notes; the newest file's headers."""
    cats = ['https://thredds.met.no/thredds/catalog/remotesensingradar/catalog.html',
            'https://thredds.met.no/thredds/catalog/remotesensing/reflectivity-nordic/catalog.html',
            'https://thredds.met.no/thredds/catalog/remotesensingradaraccr/catalog.html',
            'https://thredds.met.no/thredds/catalog/remotesensing/catalog.html',
            'https://thredds.met.no/thredds/catalog/radar/catalog.html',
            'https://thredds.met.no/thredds/catalog.html']
    for c in cats:
        try:
            r = requests.get(c, headers={'User-Agent': UA}, timeout=60)
            names = re.findall(r'<a href="[^"]*">\s*<tt>([^<]+)</tt>', r.text) or re.findall(r'>([^<]{3,120}\.nc)<', r.text)
            log('thredds', r.status_code, c, 'entries', len(names), names[:12], names[-6:] if len(names) > 12 else '')
        except Exception as e:
            log('thredds', c, 'failed:', e)
    # the newest file of the reflectivity-nordic / remotesensingradar catalogue: walk one level (a year / month folder) and read the header
    for base in ('https://thredds.met.no/thredds/catalog/remotesensingradar/', 'https://thredds.met.no/thredds/catalog/remotesensing/reflectivity-nordic/'):
        try:
            r = requests.get(base + 'catalog.html', headers={'User-Agent': UA}, timeout=60)
            if r.status_code != 200:
                continue
            subs = re.findall(r'<a href="([^"]+/catalog\.html)">', r.text)
            files = re.findall(r'dataset=([^"&]+\.nc)', r.text)
            log('walk', base, 'subfolders', len(subs), subs[:4], subs[-3:], 'files here', len(files), files[-3:])
            cur = base; depth = 0
            while subs and depth < 3:
                nxt = subs[-1] if 'catalog.html' in subs[-1] else subs[0]
                cur = cur + nxt if not nxt.startswith('http') else nxt
                r = requests.get(cur, headers={'User-Agent': UA}, timeout=60)
                subs = re.findall(r'<a href="([^"]+/catalog\.html)">', r.text); files = re.findall(r'dataset=([^"&]+\.nc)', r.text)
                log('walk', cur, r.status_code, 'subfolders', len(subs), subs[-3:], 'files', len(files), files[-3:]); depth += 1
            if files:
                f = files[-1]
                das = requests.get('https://thredds.met.no/thredds/dodsC/' + f + '.das', headers={'User-Agent': UA}, timeout=60)
                log('das', f, das.status_code, re.sub(r'\s+', ' ', das.text)[:1800])
                dds = requests.get('https://thredds.met.no/thredds/dodsC/' + f + '.dds', headers={'User-Agent': UA}, timeout=60)
                log('dds', dds.status_code, re.sub(r'\s+', ' ', dds.text)[:800])
        except Exception as e:
            log('walk', base, 'failed:', e)
    return 0


GEO = 'https://dataset.api.hub.geosphere.at/v1'


def probe_austria():
    """GeoSphere Austria: the API's description of the grid endpoint and the INCA nowcast's metadata (parameters, grid, times)."""
    try:
        spec = get(GEO + '/openapi.json').json()
        for path, ops in spec.get('paths', {}).items():
            if '/grid/' in path and '{' in path:
                for meth, op in ops.items():
                    params = [(q.get('name'), q.get('required', False), str(q.get('schema', {}).get('type')), str(q.get('description', ''))[:80]) for q in op.get('parameters', [])]
                    log('grid endpoint', meth, path, params)
    except Exception as e:
        log('openapi not read:', e)
    try:
        ds = get(GEO + '/datasets').json()
        names = sorted(k for k in ds.keys() if re.search(r'radar|inca|nowcast|precip|rr', k, re.I))
        log('datasets with radar/inca/nowcast in the name:', names[:40])
        for k in names[:12]:
            v = ds[k]; log('  ', k, {kk: str(vv)[:60] for kk, vv in (v.items() if isinstance(v, dict) else [])} if isinstance(v, dict) else str(v)[:120])
    except Exception as e:
        log('datasets not read:', e)
    for rid in ('nowcast-v1-15min-1km', 'inca-v1-15min-1km'):
        for mode in ('forecast', 'historical'):
            try:
                r = requests.get(GEO + '/grid/%s/%s/metadata' % (mode, rid), headers={'User-Agent': UA}, timeout=60)
                if r.status_code != 200:
                    log('metadata', mode, rid, r.status_code, r.text[:160]); continue
                j = r.json()
                log('metadata', mode, rid, 'keys', list(j.keys())[:20])
                log('   parameters', [(p.get('name'), p.get('unit')) for p in j.get('parameters', [])][:20])
                for k in ('bbox', 'bbox_outer', 'spatial_resolution_m', 'crs', 'grid_bounds', 'frequency', 'start_time', 'end_time', 'timestamps', 'time_range',
                          'forecast_length', 'max_forecast_offset', 'last_forecast_reftime', 'available_forecast_reftimes', 'type', 'mode', 'response_formats'):
                    if k in j:
                        v = j[k]; log('  ', k, (v[:3] + ['…'] + v[-3:]) if isinstance(v, list) and len(v) > 8 else v)
            except Exception as e:
                log('metadata', mode, rid, 'failed:', e)
    # small grid asks, netcdf: the newest run and the one before (forecast_offset=1) — which times, which lead times? Does start=end=reftime
    # hand out the analysis step (lead 0), which the plain ask clips away as past?
    try:
        ref = get(GEO + '/grid/forecast/nowcast-v1-15min-1km/metadata').json().get('last_forecast_reftime', '')
        reft = parse_time(ref)
    except Exception as e:
        log('reftime not read:', e); reft = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    f = '%Y-%m-%dT%H:%M'
    asks = (('forecast', {}), ('forecast', {'forecast_offset': 1}),
            ('forecast', {'start': reft.strftime(f), 'end': reft.strftime(f)}),
            ('forecast', {'start': reft.strftime(f), 'end': (reft + dt.timedelta(minutes=15)).strftime(f)}),
            ('forecast', {'forecast_offset': 1, 'start': (reft - dt.timedelta(minutes=15)).strftime(f), 'end': (reft - dt.timedelta(minutes=15)).strftime(f)}),
            ('historical', {'start': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).strftime(f), 'end': dt.datetime.now(dt.timezone.utc).strftime(f)}))
    for mode, extra in asks:
        rid = 'inca-v1-15min-1km' if mode == 'historical' else 'nowcast-v1-15min-1km'
        try:
            r = requests.get(GEO + '/grid/%s/%s' % (mode, rid), params={'parameters': 'rr', 'bbox': '47.0,12.0,47.5,12.5', 'output_format': 'netcdf'} | extra, headers={'User-Agent': UA}, timeout=90)
            log('grid ask', mode, extra, r.status_code, r.headers.get('content-type'), len(r.content), r.text[:300] if r.status_code != 200 else '')
            if r.status_code == 200 and (r.content[:3] == b'CDF' or r.content[:4] == b'\x89HDF' or 'netcdf' in str(r.headers.get('content-type', '')).lower()):
                try:
                    import netCDF4
                    ds = netCDF4.Dataset('inmemory.nc', memory=r.content)
                    log('   variables', {k: (v.dimensions, v.shape) for k, v in ds.variables.items()})
                    for k in ('time', 'leadtime', 'rr'):
                        if k in ds.variables:
                            v = ds.variables[k][:]; log('   ', k, getattr(v, 'shape', None), list(np.ravel(v)[:14]) if k != 'rr' else (float(np.nanmin(v)), float(np.nanmax(v))), {kk: str(vv)[:90] for kk, vv in ds.variables[k].__dict__.items() if kk in ('units', 'long_name', 'standard_name', 'description', 'cell_method')})
                    log('   global', {k: str(v)[:120] for k, v in ds.__dict__.items() if k in ('last_forecast_reftime', 'available_forecast_reftimes', 'forecast_freq', 'last_forecast_slot', 'buffer_slot_size', 'title', 'source')})
                except Exception as e:
                    log('   netcdf not read:', e)
        except Exception as e:
            log('grid ask', mode, extra, 'failed:', e)
    return 0


def probe():
    j = get(SMHI).json()
    log('smhi keys:', list(j.keys()))
    for key in ('lastFiles', 'files'):
        for f in (j.get(key) or [])[-3:]:
            log('  ', key, json.dumps(f)[:400])
    r = smhi_latest()
    log('latest:', r and (r[0].isoformat(), r[1]))
    if r:
        blob = get(r[1]).content
        data, frame = read_geotiff(blob)
        log('geotiff:', data.shape, data.dtype, 'frame', frame, 'values', int(data.min()), int(data.max()), 'nodata share', round(float(np.mean(data == 255)), 3))
        inv = Transformer.from_crs(CRS.from_epsg(frame['epsg']), CRS.from_epsg(4326), always_xy=True)
        h, w = data.shape
        for name, (px, py) in (('top-left', (0, 0)), ('top-right', (w, 0)), ('bottom-left', (0, h)), ('bottom-right', (w, h)), ('centre', (w / 2, h / 2))):
            lon, lat = inv.transform(frame['x0'] + px * frame['xs'], frame['y0'] - py * frame['ys'])
            log('  corner', name, round(lon, 3), round(lat, 3))
        rate = smhi_rate(data); wet = np.argwhere(rate >= 0.1)
        if len(wet):
            r0, c0 = wet.mean(axis=0); lon, lat = inv.transform(frame['x0'] + (c0 + .5) * frame['xs'], frame['y0'] - (r0 + .5) * frame['ys'])
            log('  rain centroid', round(lon, 3), round(lat, 3), 'wet pixels', len(wet))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--step', type=float, default=0.01); ap.add_argument('--only', default='')
    ap.add_argument('--probe', action='store_true'); ap.add_argument('--file', help='a local GeoTIFF instead of the download (for a try)')
    ap.add_argument('--backfill', type=int, default=12, help='how many of the newest pictures (SMHI) or runs (GeoSphere, at most six on offer) to look at per pass')
    a = ap.parse_args()
    only = [x for x in a.only.split(',') if x]
    if a.probe:
        return probe() if (not only or 'se' in only) else (probe_austria() if 'at' in only else (probe_thredds() if 'thredds' in only else (probe_dmi() if 'dmi' in only else (probe_chmi() if 'cz' in only else (probe_dwd() if 'de' in only else (probe_dpc() if 'it' in only else probe_norway()))))))
    os.makedirs(a.out, exist_ok=True)
    for src in SOURCES:
        if only and src['id'] not in only:
            continue
        if src.get('retired') and src['id'] not in only:
            log(src['id'], 'retired —', src['retired']); continue
        if src.get('await_probe') and src['id'] not in only:
            log(src['id'], 'waits for its probe —', src['await_probe']); continue
        d = a.out + '/' + src['id']; os.makedirs(d, exist_ok=True)
        if src.get('kind') in ('geosphere', 'nordic', 'dmi', 'knmi', 'meteofrance', 'chmi', 'dpc', 'aemet', 'imo', 'dwd'):
            try:
                {'geosphere': run_geosphere, 'nordic': run_nordic, 'dmi': run_dmi, 'knmi': run_knmi, 'meteofrance': run_meteofrance, 'chmi': run_chmi, 'dpc': run_dpc, 'aemet': run_aemet, 'imo': run_imo, 'dwd': run_dwd}[src['kind']](src, a)
            except Exception as e:
                log(src['id'], 'failed:', e)
                json.dump({'ok': False, 'error': str(e)[:300], 'at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}, open(d + '/status.json', 'w'), indent=1)
            continue
        try:
            if a.file:
                blob = open(a.file, 'rb').read(); stamp = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0); href = a.file
                todo = [(stamp, href)]
            else:
                todo = [(t, h) for t, h in smhi_files(max(1, a.backfill)) if not os.path.exists(d + '/frames/' + t.strftime('%Y%m%d%H%M') + '.png')]
                if not todo:
                    lst = smhi_files(1)
                    if not lst:
                        raise RuntimeError('no file named by the service')
                    log(src['id'], 'already there', lst[-1][0].strftime('%Y%m%d%H%M')); continue
            got = 0
            for stamp, href in todo:                                          # oldest first, so latest.png ends with the newest
                try:
                    blob = open(href, 'rb').read() if a.file else get(href).content
                    log(src['id'], 'file', href, len(blob), 'bytes, time', stamp.isoformat())
                    data, frame = read_geotiff(blob)
                    rate = smhi_rate(data)
                    field, size = reproject_grid(rate, frame, src['bbox'], a.step)
                    img = convert.paint(field)
                    wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
                    if convert.coverage_stale(d, stamp):
                        convert.write_coverage(d, np.isfinite(field), src['bbox'], a.step, src['source'])      # the reach's edge (6 Nov, late)
                    k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': a.step, 'url': href})
                    log('  done', k, size, 'wet share', round(wet, 4), 'frames', n); got += 1
                except Exception as e:
                    log('  ', stamp.strftime('%H:%MZ'), 'failed:', e)
            if not got:
                raise RuntimeError('no picture converted')
        except Exception as e:
            log(src['id'], 'failed:', e)
            json.dump({'ok': False, 'error': str(e)[:300], 'at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}, open(d + '/status.json', 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
