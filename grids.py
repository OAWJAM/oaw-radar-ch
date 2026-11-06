#!/usr/bin/env python3
"""The national radars that come as a georeferenced grid rather than a WMS — SMHI's Swedish composite (a GeoTIFF in SWEREF99 TM, EPSG:3006) —
reprojected onto plain latitude/longitude and painted with the same scale as the Swiss picture, under <out>/<id>/ like the WMS sources.

  python3 grids.py [--out site] [--frames 12] [--only se] [--probe]

Sources (keyless, open data):
  SMHI  https://opendata-download-radar.smhi.se/api   version/latest/area/sweden/product/comp — a PNG and a GeoTIFF every five minutes;
        pixel value v (1…254) → dBZ = 0.4·v − 30 (0 = no echo, 255 = no data); rain rate by Marshall–Palmer Z = 200·R^1.6.
"""
import argparse, datetime as dt, io, json, os, re, sys, time
import numpy as np
import requests
from PIL import Image
from pyproj import CRS, Transformer
import convert

UA = convert.UA
SMHI = 'https://opendata-download-radar.smhi.se/api/version/latest/area/sweden/product/comp'
SOURCES = [
    {'id': 'se', 'name': 'Sweden', 'source': 'SMHI', 'licence': 'CC BY 4.0, Source: SMHI', 'product': 'COMP', 'bbox': (9.5, 54.5, 25.0, 69.5), 'step_min': 5},
]


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def get(url, **kw):
    r = requests.get(url, headers={'User-Agent': UA, 'Accept': 'application/json, */*'}, timeout=90, **kw)
    r.raise_for_status()
    return r


def smhi_latest():
    """The newest GeoTIFF of the composite: (stamp, href), or None. The product's metadata names the last files with their formats."""
    j = get(SMHI).json()
    files = []
    for key in ('lastFiles', 'files'):
        for f in (j.get(key) or []):
            if not isinstance(f, dict):
                continue
            valid = f.get('valid') or f.get('validTime') or ''
            for fm in (f.get('formats') or []):
                if str(fm.get('key', '')).lower() in ('tif', 'tiff', 'geotiff') and fm.get('link'):
                    files.append((str(valid), fm['link']))
    if not files:
        # the day's listing as a fallback: today's folder, ?format=tif
        now = dt.datetime.now(dt.timezone.utc)
        for day in (now, now - dt.timedelta(days=1)):
            try:
                jd = get(SMHI + day.strftime('/%Y/%m/%d'), params={'format': 'tif'}).json()
            except Exception as e:
                log('day listing not read:', e); continue
            for f in (jd.get('files') or []):
                valid = f.get('valid') or ''
                for fm in (f.get('formats') or []):
                    if str(fm.get('key', '')).lower() in ('tif', 'tiff') and fm.get('link'):
                        files.append((str(valid), fm['link']))
            if files:
                break
    if not files:
        return None
    files.sort()
    valid, href = files[-1]
    stamp = parse_time(valid) if valid else dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    return stamp, href


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
    return data, {'x0': x0, 'y0': y0, 'xs': xs, 'ys': ys, 'epsg': epsg or 3006, 'nodata': tags.get('GDAL_NODATA')}


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
    to_grid = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(frame['epsg']), always_xy=True)
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
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--step', type=float, default=0.01); ap.add_argument('--only', default='')
    ap.add_argument('--probe', action='store_true'); ap.add_argument('--file', help='a local GeoTIFF instead of the download (for a try)')
    a = ap.parse_args()
    if a.probe:
        return probe()
    os.makedirs(a.out, exist_ok=True)
    only = [x for x in a.only.split(',') if x]
    for src in SOURCES:
        if only and src['id'] not in only:
            continue
        d = a.out + '/' + src['id']; os.makedirs(d, exist_ok=True)
        try:
            if a.file:
                blob = open(a.file, 'rb').read(); stamp = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0); href = a.file
            else:
                found = smhi_latest()
                if not found:
                    raise RuntimeError('no file named by the service')
                stamp, href = found
                key = stamp.strftime('%Y%m%d%H%M')
                if os.path.exists(d + '/frames/' + key + '.png'):
                    log(src['id'], 'already there', key); continue
                blob = get(href).content
            log(src['id'], 'file', href, len(blob), 'bytes, time', stamp.isoformat())
            data, frame = read_geotiff(blob)
            rate = smhi_rate(data)
            field, size = reproject_grid(rate, frame, src['bbox'], a.step)
            img = convert.paint(field)
            wet = float(np.nanmean(field >= 0.1)) if np.isfinite(field).any() else 0.0
            k, n = write_frames(a.out, src, stamp, img, a.frames, {'wet_share': round(wet, 4), 'width': size[0], 'height': size[1], 'step_deg': a.step, 'url': href})
            log('  done', k, size, 'wet share', round(wet, 4), 'frames', n)
        except Exception as e:
            log(src['id'], 'failed:', e)
            json.dump({'ok': False, 'error': str(e)[:300], 'at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}, open(d + '/status.json', 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
