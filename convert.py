#!/usr/bin/env python3
"""Radar CH — the MeteoSwiss precipitation radar (open data, CC-BY) as a transparent PNG in plain latitude/longitude
(EPSG:4326) for a web map, with a small JSON beside it and a short loop of the last frames.

Runs on GitHub Actions every ten minutes (see .github/workflows/radar.yml); nothing here is tied to one web site.

  python3 convert.py [--out site] [--frames 12] [--step 0.008] [--product rzc]

Source: MeteoSwiss, https://opendatadocs.meteoswiss.ch/d-radar-data/d1-precipitation-radar-products
  STAC collection  ch.meteoschweiz.ogd-radar-precip — one item per calendar day, id <YYYYMMDD>-ch,
  assets           https://data.geo.admin.ch/ch.meteoschweiz.ogd-radar-precip/<YYYYMMDD>-ch/<file>
  RZC              rain rate (mm/h), every 5 min, ODIM HDF5, Swiss LV95 (EPSG:2056), file rzcYYJJJHHMM*.h5
"""
import argparse, datetime as dt, io, json, os, re, sys, time
import numpy as np
import requests
import h5py
from PIL import Image
from pyproj import CRS, Transformer

STAC = 'https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-radar-precip/items/'
UA = 'oaw-radar-ch converter (github actions; contact via the repository)'
# the output window in degrees (W, S, E, N): Switzerland and its surroundings, the composite's own reach
BBOX = (5.5, 45.5, 11.0, 48.2)
# the colour scale (mm/h → RGBA), the usual steps of a precipitation radar
SCALE = [(0.1, (173, 216, 255, 170)), (0.3, (120, 180, 255, 190)), (1.0, (60, 120, 255, 205)), (3.0, (20, 200, 120, 215)),
         (10.0, (255, 220, 0, 225)), (30.0, (255, 140, 0, 235)), (100.0, (230, 30, 30, 245)), (1e9, (190, 0, 190, 255))]
# MeteoSwiss' 8-bit rain-rate table (metranet RZC/CPC: 10^((x-71)/20) mm/h, 0 = no rain, 255 = no data), used only when the
# ODIM attributes say the data are raw (gain 1, offset 0) on 8 bits
TABLE = np.zeros(256, dtype=np.float32)
TABLE[1:255] = 10.0 ** ((np.arange(1, 255) - 71.0) / 20.0)
TABLE[255] = np.nan


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def fetch(url, **kw):
    r = requests.get(url, headers={'User-Agent': UA}, timeout=60, **kw)
    r.raise_for_status()
    return r


def newest_asset(product: str, when: dt.datetime):
    """The newest asset of the product in today's item (yesterday's around midnight): (name, href, stamp)."""
    best = None
    for day in (when, when - dt.timedelta(days=1)):
        item = day.strftime('%Y%m%d') + '-ch'
        try:
            j = fetch(STAC + item).json()
        except Exception as e:                      # the next day's item exists from midnight on; a missing one is no error
            log('item', item, 'not read:', e)
            continue
        for name, a in (j.get('assets') or {}).items():
            m = re.match(r'^' + product + r'(\d{2})(\d{3})(\d{2})(\d{2})', name, re.I)
            if not m:
                continue
            yy, jjj, hh, mm = (int(x) for x in m.groups())
            stamp = dt.datetime(2000 + yy, 1, 1, hh, mm, tzinfo=dt.timezone.utc) + dt.timedelta(days=jjj - 1)
            if best is None or stamp > best[2] or (stamp == best[2] and name > best[0]):
                best = (name, a.get('href'), stamp)
        if best is not None:
            break
    return best


def read_odim(blob: bytes):
    """The rain-rate field (mm/h, NaN where unknown) and its grid in the file's own projection."""
    with h5py.File(io.BytesIO(blob), 'r') as f:
        where = dict(f['where'].attrs)
        d = f['dataset1/data1']
        raw = d['data'][...]
        what = dict(d['what'].attrs) if 'what' in d else {}
        dwhat = dict(f['dataset1/what'].attrs) if 'what' in f['dataset1'] else {}
        top = dict(f['what'].attrs)
    def s(v):
        return v.decode() if isinstance(v, bytes) else v
    gain = float(what.get('gain', 1.0)); offset = float(what.get('offset', 0.0))
    nodata = what.get('nodata'); undetect = what.get('undetect')
    quantity = s(what.get('quantity', dwhat.get('quantity', '')))
    log('odim: quantity', quantity, 'dtype', raw.dtype, 'shape', raw.shape, 'gain', gain, 'offset', offset, 'nodata', nodata, 'undetect', undetect,
        'product', s(dwhat.get('product', '')), 'date', s(top.get('date', '')), s(top.get('time', '')))
    if raw.dtype == np.uint8 and gain == 1.0 and offset == 0.0:
        rate = TABLE[raw]                                       # MeteoSwiss' own 8-bit table
        scale = 'metranet 8-bit table 10^((x-71)/20)'
    else:
        rate = raw.astype(np.float32) * gain + offset
        if nodata is not None:
            rate[raw == nodata] = np.nan
        if undetect is not None:
            rate[raw == undetect] = 0.0
        scale = 'gain/offset'
    proj = s(where.get('projdef', '+proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 +x_0=2600000 +y_0=1200000 +ellps=bessel +units=m'))
    grid = {'proj': proj, 'xsize': int(where.get('xsize', raw.shape[1])), 'ysize': int(where.get('ysize', raw.shape[0])),
            'xscale': float(where.get('xscale', 1000.0)), 'yscale': float(where.get('yscale', 1000.0)),
            'LL_lon': float(where.get('LL_lon', 0)), 'LL_lat': float(where.get('LL_lat', 0)), 'UR_lon': float(where.get('UR_lon', 0)), 'UR_lat': float(where.get('UR_lat', 0)),
            'UL_lon': where.get('UL_lon'), 'UL_lat': where.get('UL_lat')}
    log('odim: grid', {k: v for k, v in grid.items() if k != 'proj'}, 'proj', proj[:80])
    return rate, grid, scale, quantity


def reproject(rate: np.ndarray, grid: dict, bbox, step: float):
    """Nearest-neighbour resampling of the LV95 field onto a plate-carrée grid of `step` degrees."""
    W, S, E, N = bbox
    nx = int(round((E - W) / step)); ny = int(round((N - S) / step))
    lons = W + (np.arange(nx) + 0.5) * step
    lats = N - (np.arange(ny) + 0.5) * step
    LON, LAT = np.meshgrid(lons, lats)
    crs = CRS.from_proj4(grid['proj'])
    to_grid = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    X, Y = to_grid.transform(LON, LAT)
    # the field's frame: the lower-left and upper-right corners in the file's projection
    x0, y0 = to_grid.transform(grid['LL_lon'], grid['LL_lat'])
    x1, y1 = to_grid.transform(grid['UR_lon'], grid['UR_lat'])
    if grid['UL_lon'] is not None and grid['UL_lat'] is not None:
        _, yul = to_grid.transform(float(grid['UL_lon']), float(grid['UL_lat']))
        y1 = max(y1, yul)
    xs = grid['xscale'] if grid['xscale'] > 0 else (x1 - x0) / grid['xsize']
    ys = grid['yscale'] if grid['yscale'] > 0 else (y1 - y0) / grid['ysize']
    col = np.floor((X - x0) / xs).astype(np.int64)
    row = np.floor((y1 - Y) / ys).astype(np.int64)            # ODIM images start at the top (north)
    h, w = rate.shape
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    out = np.full((ny, nx), np.nan, dtype=np.float32)
    out[inside] = rate[row[inside], col[inside]]
    return out, (W, S, E, N), (nx, ny)


def paint(field: np.ndarray) -> Image.Image:
    ny, nx = field.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    v = np.nan_to_num(field, nan=-1.0)
    lo = 0.0
    for hi, colour in SCALE:
        sel = (v >= max(lo, 0.1)) & (v < hi)
        rgba[sel] = colour
        lo = hi
    return Image.fromarray(rgba, 'RGBA')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--step', type=float, default=0.008); ap.add_argument('--product', default='rzc')
    ap.add_argument('--file', help='a local ODIM file instead of the STAC lookup (for a try)')
    a = ap.parse_args()
    os.makedirs(a.out + '/frames', exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    if a.file:
        blob = open(a.file, 'rb').read(); name = os.path.basename(a.file); stamp = now.replace(second=0, microsecond=0); href = a.file
    else:
        found = newest_asset(a.product, now)
        if not found:
            log('no', a.product, 'asset today — nothing to do'); write_status(a.out, now, None, 'no asset found'); return 0
        name, href, stamp = found
        log('newest', name, stamp.isoformat(), href)
        key = stamp.strftime('%Y%m%d%H%M')
        if os.path.exists(a.out + '/frames/' + key + '.png') and os.path.exists(a.out + '/latest.json'):
            try:
                if json.load(open(a.out + '/latest.json')).get('file') == name:
                    log('already converted', name); return 0
            except Exception:
                pass
        blob = fetch(href).content
        log('downloaded', len(blob), 'bytes')
    rate, grid, scale, quantity = read_odim(blob)
    field, bbox, size = reproject(rate, grid, BBOX, a.step)
    img = paint(field)
    key = stamp.strftime('%Y%m%d%H%M')
    img.save(a.out + '/frames/' + key + '.png', optimize=True)
    img.save(a.out + '/latest.png', optimize=True)
    wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
    meta = {'product': a.product.upper(), 'quantity': quantity, 'file': name, 'source': 'MeteoSwiss', 'licence': 'CC-BY, Source: MeteoSwiss',
            'time': stamp.strftime('%Y-%m-%dT%H:%MZ'), 'converted': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'bbox': {'west': bbox[0], 'south': bbox[1], 'east': bbox[2], 'north': bbox[3]},
            'crs': 'EPSG:4326', 'width': size[0], 'height': size[1], 'step_deg': a.step, 'scale': scale, 'wet_share': round(wet, 4),
            'legend': [{'from_mmh': (SCALE[i - 1][0] if i else 0.1), 'to_mmh': (hi if hi < 1e8 else None), 'rgba': list(c)} for i, (hi, c) in enumerate(SCALE)],
            'url': href}
    json.dump(meta, open(a.out + '/latest.json', 'w'), indent=1)
    # the loop: the newest frames, the rest pruned
    frames = sorted(f for f in os.listdir(a.out + '/frames') if re.match(r'^\d{12}\.png$', f))
    for f in frames[:-a.frames] if len(frames) > a.frames else []:
        os.remove(a.out + '/frames/' + f)
    frames = frames[-a.frames:]
    json.dump({'product': a.product.upper(), 'bbox': meta['bbox'], 'crs': 'EPSG:4326', 'updated': meta['converted'], 'source': 'MeteoSwiss',
               'frames': [{'time': f[:4] + '-' + f[4:6] + '-' + f[6:8] + 'T' + f[8:10] + ':' + f[10:12] + 'Z', 'src': 'frames/' + f} for f in frames]},
              open(a.out + '/frames.json', 'w'), indent=1)
    write_status(a.out, now, meta, '')
    write_index(a.out)
    log('done:', key, size, 'wet share', round(wet, 4), 'frames', len(frames))
    return 0


def write_status(out, now, meta, error):
    json.dump({'ok': not error, 'error': error, 'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'latest': meta and meta.get('time')}, open(out + '/status.json', 'w'), indent=1)


def write_index(out):
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Radar CH</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font:14px/1.45 system-ui,sans-serif;margin:16px;color:#111;background:#f6f7f9}img{max-width:100%;background:#dfe6ee;border:1px solid #ccd}code{font-size:12px}small{color:#555}</style></head>
<body><h1 style="font-size:18px;margin:0 0 6px">Radar CH — precipitation (mm/h)</h1>
<p><small>Source: MeteoSwiss open data (CC-BY). Converted every ten minutes by GitHub Actions; a picture in plain latitude/longitude for a web map — not for operational use.</small></p>
<p id="t"></p><img id="i" src="latest.png" alt="radar"><p><code>latest.png · latest.json · frames.json · status.json</code></p>
<script>fetch('latest.json',{cache:'no-store'}).then(r=>r.json()).then(m=>{document.getElementById('t').textContent='picture '+m.time+' · '+m.product+' · '+m.width+'×'+m.height+' px · bbox '+[m.bbox.west,m.bbox.south,m.bbox.east,m.bbox.north].join(', ')}).catch(()=>{});</script>
</body></html>"""
    open(out + '/index.html', 'w').write(html)
    open(out + '/.nojekyll', 'w').write('')


if __name__ == '__main__':
    sys.exit(main())
