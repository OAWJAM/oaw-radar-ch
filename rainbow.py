#!/usr/bin/env python3
"""Rainbow.ai's world-wide radar mosaic as a source of this site (7 Nov 2026 — the owner: «build it in the background, ready to activate»).

Nothing happens without the secret RAINBOW_API_KEY (Settings → Secrets and variables → Actions → New repository secret): the
run logs «no key — skipped» and stays green. Repository *variables* (same page, the Variables tab) steer it without a code change:

  RAINBOW_LAYER          radars (default) · precip-global · precip         — which of Rainbow's tile layers
  RAINBOW_AREAS          world:3 (default)                                  — a comma list of <name>:<zoom>[:<x0>-<x1>:<y0>-<y1>];
                                                                              presets: world:3 (64 tiles), europe:5 (48 tiles); both: world:3,europe:5
  RAINBOW_CADENCE_MIN    30 (default)                                       — a new picture only when the newest is at least this old
  RAINBOW_MONTHLY_TILES  100000 (default)                                   — the month's tile budget; reached → skipped until the next month
  RAINBOW_COLOR          dbz_u8 (default)                                   — the palette asked of the API: dbz_u8 carries raw dBZ in the red
                                                                              channel and is painted in this site's own scale; a number (0–9)
                                                                              keeps Rainbow's own picture as it is

What a run does: asks the layer's newest snapshot (one small request), and when it is newer than the newest picture kept and
the cadence allows, fetches the area's tiles (Web Mercator, XYZ), stitches them, decodes the dBZ, paints the mosaic, and
writes it like every other source: <area>/frames/<stamp>.png, latest.png, frames.json (crs EPSG:3857 — the boards lay a
Mercator picture on their Mercator chart in one piece), status.json, coverage.json (the edge of the radars' reach from the
transparent pixels) and usage.json (the month's tile count — the budget's memory, carried along in the site cache).

The key travels in a header, never in a URL, so it never lands in a log.
"""
import argparse, datetime as dt, io, json, math, os, re, sys, time
import numpy as np
import requests
from PIL import Image
import convert

API = 'https://api.rainbow.ai/tiles/v1'
UA = convert.UA
LAYERS = {'radars': 'Radar mosaic', 'precip-global': 'Precipitation (radar, satellite and nowcast)', 'precip': 'Precipitation'}
PRESETS = {'world': (3, 0, 7, 0, 7), 'europe': (5, 14, 21, 8, 13)}       # name → zoom, x0, x1, y0, y1 (inclusive); europe: 22.5 W – 67.5 E, 21.9 – 66.5 N
TILE = 256
SOURCE = 'Rainbow.ai'
LICENCE = 'Rainbow Weather API (rainbow.ai) — the site owner\'s subscription; attribution «Rainbow.ai»'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def env_int(name, default):
    try:
        return int(os.environ.get(name, '') or default)
    except ValueError:
        return default


def areas_wanted(spec):
    """RAINBOW_AREAS → [(name, zoom, x0, x1, y0, y1)]; a bad entry is logged and skipped."""
    out = []
    for item in (spec or 'world:3').split(','):
        item = item.strip().lower()
        if not item:
            continue
        p = item.split(':')
        name = re.sub(r'[^a-z0-9]', '', p[0])[:10]
        if not name:
            log('area skipped (no name):', item); continue
        try:
            if len(p) == 1 and name in PRESETS:
                z, x0, x1, y0, y1 = PRESETS[name]
            elif len(p) == 2 and name in PRESETS:
                z = int(p[1]); pz, px0, px1, py0, py1 = PRESETS[name]
                if z == pz:
                    x0, x1, y0, y1 = px0, px1, py0, py1
                else:                                                     # the preset's window at another zoom
                    f = 2 ** (z - pz)
                    x0, x1, y0, y1 = (int(px0 * f), int((px1 + 1) * f) - 1, int(py0 * f), int((py1 + 1) * f) - 1) if f >= 1 else (int(px0 * f), int(px1 * f), int(py0 * f), int(py1 * f))
            elif len(p) == 2:
                z = int(p[1]); n = 2 ** z; x0, x1, y0, y1 = 0, n - 1, 0, n - 1
            else:
                z = int(p[1]); xs = p[2].split('-'); ys = p[3].split('-')
                x0, x1, y0, y1 = int(xs[0]), int(xs[-1]), int(ys[0]), int(ys[-1])
        except (ValueError, IndexError):
            log('area skipped (unreadable):', item); continue
        n = 2 ** z
        if not (0 <= z <= 12) or not (0 <= x0 <= x1 < n) or not (0 <= y0 <= y1 < n):
            log('area skipped (out of range):', item); continue
        out.append((name, z, x0, x1, y0, y1))
    return out


def tile_lon(x, z):
    return x / 2 ** z * 360.0 - 180.0


def tile_lat(y, z):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def area_bbox(z, x0, x1, y0, y1):
    return (tile_lon(x0, z), tile_lat(y1 + 1, z), tile_lon(x1 + 1, z), tile_lat(y0, z))


class Rainbow:
    def __init__(self, key, layer, color):
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': UA, 'Ocp-Apim-Subscription-Key': key})
        self.layer = layer; self.color = color
        self.requests = 0

    def snapshot(self):
        self.requests += 1
        r = self.s.get(API + '/snapshot', params={'layer': self.layer}, timeout=60)
        r.raise_for_status()
        return int(r.json()['snapshot'])

    def tile(self, snapshot, z, x, y):
        self.requests += 1
        path = '/%s/%d/%s%d/%d/%d' % (self.layer, snapshot, '' if self.layer == 'radars' else '0/', z, x, y)   # the precip layers carry a forecast offset (0 = now)
        params = {'coverage': 1}
        if self.color != '':
            params['color'] = self.color
        r = self.s.get(API + path, params=params, timeout=60)
        if r.status_code == 404:
            return None                                                     # no tile: nothing there (an ocean tile without radar, say)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert('RGBA')


def stitch(rb, snapshot, z, x0, x1, y0, y1):
    """The area's tiles as one RGBA array (rows × cols of TILE pixels), plus how many tiles came back."""
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    size = None; got = 0; mosaic = None
    for j, y in enumerate(range(y0, y1 + 1)):
        for i, x in enumerate(range(x0, x1 + 1)):
            im = rb.tile(snapshot, z, x, y)
            if im is None:
                continue
            if size is None:
                size = im.size[0]
                mosaic = np.zeros((ny * size, nx * size, 4), dtype=np.uint8)
            if im.size[0] != size:
                im = im.resize((size, size))
            mosaic[j * size:(j + 1) * size, i * size:(i + 1) * size] = np.asarray(im)
            got += 1
    if mosaic is None:
        mosaic = np.zeros((ny * TILE, nx * TILE, 4), dtype=np.uint8)
    return mosaic, got


def decode_dbz(mosaic):
    """dbz_u8 tiles: the red channel carries the reflectivity (bit 7 = snow, the rest dBZ + 32), alpha 0 = no radar. → (rate mm/h, snow mask, valid mask)."""
    r = mosaic[:, :, 0].astype(np.int32); a = mosaic[:, :, 3]
    valid = a > 0
    snow = valid & ((r & 128) == 128)
    dbz = ((r & 127) - 32).astype(np.float32)
    rate = (np.power(10.0, dbz / 10.0) / 200.0) ** (1.0 / 1.6)
    rate[dbz < 5.0] = 0.0
    rate[~valid] = np.nan
    return rate.astype(np.float32), snow, valid


def paint_mosaic(mosaic, color):
    """The picture to publish: our own scale from the decoded dBZ (dbz_u8), or Rainbow's picture as it came (a palette number)."""
    if color == 'dbz_u8':
        rate, snow, valid = decode_dbz(mosaic)
        img = convert.paint(rate)
        return img, valid, float(np.mean(np.nan_to_num(rate) >= 0.1)) if rate.size else 0.0
    valid = mosaic[:, :, 3] > 0
    img = Image.fromarray(mosaic, 'RGBA')
    wet = float(np.mean(mosaic[:, :, 3] > 40)) if mosaic.size else 0.0
    return img, valid, wet


def coverage_lonlat(valid, z, x0, y0, ntx, bbox, step):
    """The Mercator mask resampled to a lon/lat grid (step degrees) for the ring tracing, which thinks in degrees. ntx: tiles across."""
    W, S, E, N = bbox
    nx = int(round((E - W) / step)); ny = int(round((N - S) / step))
    lons = W + (np.arange(nx) + 0.5) * step
    lats = N - (np.arange(ny) + 0.5) * step
    LON, LAT = np.meshgrid(lons, lats)
    n = 2 ** z; h, w = valid.shape
    size = w / max(1, ntx)                                                        # the pixels per tile as they came (256 or 512)
    gx = ((LON + 180.0) / 360.0 * n - x0) * size
    gy = ((1.0 - np.log(np.tan(np.pi / 4 + np.radians(LAT) / 2)) / np.pi) / 2.0 * n - y0) * size
    col = np.floor(gx).astype(np.int64); row = np.floor(gy).astype(np.int64)
    inside = (col >= 0) & (col < w) & (row >= 0) & (row < h)
    out = np.zeros((ny, nx), dtype=bool)
    out[inside] = valid[row[inside], col[inside]]
    return out


def write_area(out, name, z, x0, x1, y0, y1, stamp, img, valid, keep, wet, note):
    d = out + '/rb-' + name
    os.makedirs(d + '/frames', exist_ok=True)
    key = stamp.strftime('%Y%m%d%H%M')
    img.save(d + '/frames/' + key + '.png', optimize=True)
    img.save(d + '/latest.png', optimize=True)
    frames = sorted(f for f in os.listdir(d + '/frames') if re.match(r'^\d{12}\.png$', f))
    for f in frames[:-keep] if len(frames) > keep else []:
        os.remove(d + '/frames/' + f)
    frames = frames[-keep:]
    W, S, E, N = area_bbox(z, x0, x1, y0, y1)
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump({'product': note, 'name': ('World' if name == 'world' else name.capitalize()) + ' · Rainbow.ai', 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:3857',
               'zoom': z, 'tiles': {'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1}, 'updated': now, 'source': SOURCE, 'licence': LICENCE,
               'legend': [{'from_mmh': (convert.SCALE[i - 1][0] if i else 0.1), 'to_mmh': (hi if hi < 1e8 else None), 'rgba': list(c)} for i, (hi, c) in enumerate(convert.SCALE)],
               'frames': [{'time': f[:4] + '-' + f[4:6] + '-' + f[6:8] + 'T' + f[8:10] + ':' + f[10:12] + 'Z', 'src': 'frames/' + f} for f in frames],
               'wet_share': round(wet, 4)}, open(d + '/frames.json', 'w'), indent=1)
    # the coverage edge from the transparent pixels, on a lon/lat grid a quarter (world) or a twentieth (a zoomed area) of a degree wide
    if convert.coverage_stale(d, stamp):
        try:
            step = 0.25 if z <= 3 else (0.1 if z <= 5 else 0.05)
            ll = coverage_lonlat(valid, z, x0, y0, x1 - x0 + 1, (W, S, E, N), step)
            rings = convert.coverage_rings(ll, (W, S, E, N), step, coarse=step * 2, min_len=10)
            rings = sorted(rings, key=len, reverse=True)[:40]
            if rings:
                json.dump({'v': convert.COVERAGE_V, 'source': SOURCE, 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:4326', 'rings': rings,
                           'note': 'the edge of the radars\' reach, traced from the transparent pixels of Rainbow.ai\'s mosaic'}, open(d + '/coverage.json', 'w'))
                log(name, 'coverage traced:', len(rings), 'rings')
        except Exception as e:
            log(name, 'coverage not traced:', e)
    return key, len(frames)


def usage_load(d):
    try:
        u = json.load(open(d + '/usage.json'))
        return {'month': str(u.get('month', '')), 'tiles': int(u.get('tiles', 0)), 'runs': int(u.get('runs', 0))}
    except Exception:
        return {'month': '', 'tiles': 0, 'runs': 0}


def usage_save(d, u):
    os.makedirs(d, exist_ok=True)
    json.dump(u, open(d + '/usage.json', 'w'))


def status(d, ok, error, **extra):
    os.makedirs(d, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    old = {}
    try:
        old = json.load(open(d + '/status.json'))
    except Exception:
        pass
    json.dump({'ok': ok, 'error': error, 'at': now, 'latest': old.get('latest', '')} | extra, open(d + '/status.json', 'w'), indent=1)


def newest_kept(d):
    try:
        frames = sorted(f for f in os.listdir(d + '/frames') if re.match(r'^\d{12}\.png$', f))
        if frames:
            return dt.datetime.strptime(frames[-1][:12], '%Y%m%d%H%M').replace(tzinfo=dt.timezone.utc)
    except Exception:
        pass
    return None


def probe(rb):
    """With a key: what each layer answers — the snapshot, one tile of the world at zoom 2 (size, mode, the red channel's spread), the dbz_u8 palette."""
    for layer in LAYERS:
        try:
            rb.layer = layer
            snap = rb.snapshot()
            log(layer, 'snapshot', snap, dt.datetime.fromtimestamp(snap, dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
            for color in ('dbz_u8', '0'):
                rb.color = color
                im = rb.tile(snap, 2, 1, 1)
                if im is None:
                    log('  ', layer, 'color', color, 'tile 2/1/1: 404'); continue
                a = np.asarray(im)
                log('  ', layer, 'color', color, 'tile 2/1/1:', im.size, im.mode, 'alpha>0 share %.3f' % float(np.mean(a[:, :, 3] > 0)), 'red min/max', int(a[:, :, 0].min()), int(a[:, :, 0].max()))
        except Exception as e:
            log(layer, 'failed:', e)
    log('requests spent by the probe:', rb.requests)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--probe', action='store_true', help='with a key: what the layers answer, nothing written')
    ap.add_argument('--force', action='store_true', help='fetch even within the cadence (a look)')
    a = ap.parse_args()
    key = os.environ.get('RAINBOW_API_KEY', '').strip()
    layer = (os.environ.get('RAINBOW_LAYER', '') or 'radars').strip().lower()
    color = (os.environ.get('RAINBOW_COLOR', '') or 'dbz_u8').strip()
    cadence = env_int('RAINBOW_CADENCE_MIN', 30)
    budget = env_int('RAINBOW_MONTHLY_TILES', 100000)
    areas = areas_wanted(os.environ.get('RAINBOW_AREAS', ''))
    if not key:
        log('Rainbow.ai: no key — skipped (the secret RAINBOW_API_KEY switches the source on)')
        return 0
    if layer not in LAYERS:
        log('Rainbow.ai: unknown layer', layer, '— one of', ', '.join(LAYERS)); return 0
    if not re.match(r'^(dbz_u8|\d)$', color):
        log('Rainbow.ai: unknown color', color, '— dbz_u8 or a palette number'); color = 'dbz_u8'
    rb = Rainbow(key, layer, color)
    if a.probe:
        probe(rb); return 0
    if not areas:
        log('Rainbow.ai: no area to fetch'); return 0
    now = dt.datetime.now(dt.timezone.utc)
    month = now.strftime('%Y-%m')
    try:
        snap = rb.snapshot()
    except Exception as e:
        log('Rainbow.ai: snapshot failed:', e)
        for name, *_ in areas:
            status(a.out + '/rb-' + name, False, 'snapshot: ' + str(e)[:200])
        return 0
    stamp = dt.datetime.fromtimestamp(snap, dt.timezone.utc)
    log('Rainbow.ai: layer', layer, 'snapshot', stamp.strftime('%Y-%m-%dT%H:%MZ'), 'color', color, 'cadence', cadence, 'min; budget', budget, 'tiles/month')
    for name, z, x0, x1, y0, y1 in areas:
        d = a.out + '/rb-' + name
        n_tiles = (x1 - x0 + 1) * (y1 - y0 + 1)
        u = usage_load(d)
        if u['month'] != month:
            u = {'month': month, 'tiles': 0, 'runs': 0}
        u['tiles'] += 1                                                         # the snapshot call, shared by the areas but counted on each to stay on the safe side
        newest = newest_kept(d)
        if newest is not None and newest >= stamp:
            log(name, 'kept picture', newest.strftime('%H:%MZ'), 'is the snapshot — nothing new'); usage_save(d, u)
            status(d, True, '', tiles_month=u['tiles'], budget=budget, cadence_min=cadence, note='no new snapshot'); continue
        if not a.force and newest is not None and (stamp - newest) < dt.timedelta(minutes=max(1, cadence - 2)):   # the snapshot's own clock, not the run's: a late run spends nothing extra
            log(name, 'within the cadence (newest %s, snapshot %s, %d min)' % (newest.strftime('%H:%MZ'), stamp.strftime('%H:%MZ'), cadence)); usage_save(d, u)
            status(d, True, '', tiles_month=u['tiles'], budget=budget, cadence_min=cadence, note='within the cadence'); continue
        if u['tiles'] + n_tiles > budget:
            log(name, 'monthly tile budget reached (%d + %d > %d) — skipped until next month' % (u['tiles'], n_tiles, budget)); usage_save(d, u)
            status(d, False, 'monthly tile budget reached (%d of %d) — the runs skip until next month; raise RAINBOW_MONTHLY_TILES to go on' % (u['tiles'], budget),
                   tiles_month=u['tiles'], budget=budget, cadence_min=cadence); continue
        before = rb.requests
        try:
            mosaic, got = stitch(rb, snap, z, x0, x1, y0, y1)
            u['tiles'] += rb.requests - before; u['runs'] += 1
            img, valid, wet = paint_mosaic(mosaic, color)
            key, kept = write_area(a.out, name, z, x0, x1, y0, y1, stamp, img, valid, a.frames, wet, LAYERS[layer] + ' (Rainbow.ai, zoom %d)' % z)
            usage_save(d, u)
            status(d, True, '', latest=stamp.strftime('%Y-%m-%dT%H:%MZ'), tiles_month=u['tiles'], budget=budget, cadence_min=cadence, tiles_run=got, note='%d of %d tiles came back' % (got, n_tiles))
            log(name, 'done', key, '— %d of %d tiles, %d frames kept, wet share %.3f, %d tiles this month' % (got, n_tiles, kept, wet, u['tiles']))
        except Exception as e:
            u['tiles'] += rb.requests - before; usage_save(d, u)
            log(name, 'failed:', e)
            status(d, False, str(e)[:200], tiles_month=u['tiles'], budget=budget, cadence_min=cadence)
    return 0


if __name__ == '__main__':
    sys.exit(main())
