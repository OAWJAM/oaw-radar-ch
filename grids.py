#!/usr/bin/env python3
"""The national precipitation pictures that come as a georeferenced grid rather than a WMS — SMHI's Swedish radar composite (a GeoTIFF in
SWEREF99 TM, EPSG:3006) and GeoSphere Austria's INCA precipitation analysis (a NetCDF on the 1 km Austria Lambert grid, EPSG:31287) —
reprojected onto plain latitude/longitude and painted with the same scale as the Swiss picture, under <out>/<id>/ like the WMS sources.

  python3 grids.py [--out site] [--frames 12] [--only se,at] [--backfill 12] [--probe]

Sources (keyless, open data):
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
    {'id': 'se', 'kind': 'smhi', 'name': 'Sweden', 'source': 'SMHI', 'licence': 'CC BY 4.0, Source: SMHI', 'product': 'COMP', 'bbox': (9.5, 54.5, 25.0, 69.5), 'step_min': 5},
    {'id': 'at', 'kind': 'geosphere', 'name': 'Austria', 'source': 'GeoSphere Austria', 'licence': 'CC BY 4.0, Source: GeoSphere Austria', 'product': 'INCA analysis',
     'bbox': (8.1, 45.5, 17.75, 49.5), 'step_min': 15, 'note': 'INCA precipitation analysis: the radar composite blended with the rain gauges (1 km, every 15 min), the nowcast\'s lead-0 step'},
    {'id': 'dk', 'kind': 'dmi', 'name': 'Denmark', 'source': 'DMI', 'licence': 'CC BY 4.0, Source: DMI', 'product': 'Radar composite', 'bbox': (4.5, 52.5, 20.5, 59.5), 'step_min': 5, 'step': 0.02,
     'note': 'DMI\'s radar composite from the open data API (no key since 2026 — «neither registration, nor API key are needed»), ODIM HDF5 every 5 min (6 Nov, late)'},
    {'id': 'no', 'kind': 'nordic', 'name': 'Norway (Nordic composite)', 'source': 'MET Norway', 'licence': 'CC BY 4.0, Source: MET Norway', 'product': 'Nordic radar composite (pcappi 0 dBZ)',
     'bbox': (0.5, 54.0, 38.0, 72.0), 'step_min': 5, 'step': 0.02,
     'note': 'The Nordic composite from MET Norway\'s THREDDS server — the radars of Norway, Sweden and Finland on one 1 km Lambert grid every 5 min (6 Nov, late: data, not the rendered picture of api.met.no)'},
]
DMI = 'https://opendataapi.dmi.dk/v1/radardata'
THREDDS = 'https://thredds.met.no/thredds'
NORDIC_CAT = THREDDS + '/catalog/remotesensing/reflectivity-nordic/latest/catalog.html'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def get(url, **kw):
    r = requests.get(url, headers={'User-Agent': UA, 'Accept': 'application/json, */*'}, timeout=90, **kw)
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


def odim_rate(rate, quantity):
    """An ODIM field as mm/h: a RATE/RR field as it is; a reflectivity field (DBZH, TH …) through Marshall-Palmer (Z = 200 R^1.6), below 5 dBZ dry."""
    q = str(quantity or '').upper()
    if q.startswith('DBZ') or q in ('TH', 'TV', 'ZH'):
        r = (np.power(10.0, rate / 10.0) / 200.0) ** (1.0 / 1.6)
        r[rate < 5.0] = 0.0
        r[~np.isfinite(rate)] = np.nan
        return r.astype(np.float32)
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
        return probe() if (not only or 'se' in only) else (probe_austria() if 'at' in only else (probe_thredds() if 'thredds' in only else (probe_dmi() if 'dmi' in only else probe_norway())))
    os.makedirs(a.out, exist_ok=True)
    for src in SOURCES:
        if only and src['id'] not in only:
            continue
        d = a.out + '/' + src['id']; os.makedirs(d, exist_ok=True)
        if src.get('kind') in ('geosphere', 'nordic', 'dmi'):
            try:
                {'geosphere': run_geosphere, 'nordic': run_nordic, 'dmi': run_dmi}[src['kind']](src, a)
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
