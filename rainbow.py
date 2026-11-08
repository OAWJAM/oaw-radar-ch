#!/usr/bin/env python3
"""Rainbow.ai's products as sources of this site (7 Nov 2026 — the owner: «build it in the background, ready to activate»; 13:25:
«Niederschlagstypen / +4 h in 10-min-Schritten / KI-Radar und Satellit / Wolken — alles einbauen»).

Nothing happens without the secret RAINBOW_API_KEY (Settings → Secrets and variables → Actions → New repository secret): the
run logs «no key — skipped» and stays green. Repository *variables* (same page, the Variables tab) steer it without a code change:

  RAINBOW_LAYERS         precip-global,type,clouds (default)                — a comma list of products:
                           precip-global  the «AI radar»: radar + satellite + nowcast, the oceans too (zoom 0–12) — kind precip
                           precip         radar + nowcast (zoom 0–12) — kind precip
                           radars         the radar mosaic alone (zoom 0–7) — kind precip
                           type           the radar mosaic painted by precipitation type — rain, snow, mixed (zoom 0–7) — kind type
                           clouds         the cloud cover from satellite (zoom 0–7) — kind clouds
                         (RAINBOW_LAYER, the older single-layer variable, still counts when RAINBOW_LAYERS is unset)
  RAINBOW_AREAS          europe:5 (default)                                 — a comma list of <name>:<zoom>[:<x0>-<x1>:<y0>-<y1>];
                                                                              presets: europe:5 (48 tiles, 22.5 W – 67.5 E · 21.9 – 66.5 N — the Canaries,
                                                                              Egypt and the Levant inside), eu:5 (30 tiles, 22.5 W – 45 E · 32 – 66.5 N),
                                                                              world:3 (64 tiles); several: europe:5,world:3
  RAINBOW_FRAMES         0 (default)                                        — the nowcast steps in minutes for the precip products: a list «0,30,60,120,180,240», «loop» (every cadence to +240)
                                                                              or a range «0-240/10»; 0 is the picture of now, the rest go to nowcast/
  RAINBOW_CADENCE_MIN    30 (default)                                       — a new picture only when the newest is at least this old
  RAINBOW_MONTHLY_TILES  100000 (default)                                   — the month's tile budget over every product and area; reached → skipped until the next month
  RAINBOW_COLOR          dbz_u8 (default)                                   — the palette asked for the precip products: dbz_u8 carries raw dBZ in the red
                                                                              channel and is painted in this site's own scale; a number (0–9) keeps Rainbow's picture
  RAINBOW_TYPE_COLOR     0 (default)                                        — the palette of the type product (Rainbow paints rain, snow and mixed itself)
  RAINBOW_COVERAGE_MAP   1 (default)                                        — once a day the radars' coverage mask of each area as coverage-map.png (48 tiles a day)

What a run does: asks each product's newest snapshot (one small request each), and when it is newer than the newest picture kept
and the cadence allows, fetches the area's tiles (Web Mercator, XYZ), stitches them, decodes the dBZ where it can, paints the
mosaic, and writes it like every other source: rb-<area>[-type|-clouds]/frames/<stamp>.png, latest.png, frames.json (crs
EPSG:3857 and the product's kind — the boards lay a Mercator picture on their Mercator chart in one piece and draw the precip
kind alone until they have a layer for the others), status.json, coverage.json (the edge of the radars' reach from the
transparent pixels), nowcast/ (the steps beyond now) and usage.json (the month's tile count — the budget's memory).

The cost (Rainbow's list as read on 7 Nov: 30 000 tiles a month free, then 0.20 $ a thousand): tiles = products × area tiles ×
runs. europe:5 with the three default products at 30 min ≈ 207 000 tiles ≈ 35 $ a month; the 25-step nowcast loop of one
product over europe:5 at 10 min alone would be 5.2 million ≈ 1 030 $ — the levers are RAINBOW_FRAMES, RAINBOW_CADENCE_MIN and the zoom.
The key travels in a header, never in a URL, so it never lands in a log.
"""
import argparse, datetime as dt, io, json, math, os, re, sys, time
import numpy as np
import requests
from PIL import Image
import convert

API = (os.environ.get('RAINBOW_API', '') or 'https://api.rainbow.ai/tiles/v1').rstrip('/')          # a local stand-in for the bench
UA = convert.UA
LAYERS = {'radars': 'Radar mosaic', 'precip-global': 'Precipitation (radar, satellite and nowcast)', 'precip': 'Precipitation'}
# token → (the API's layer, the kind the boards see, the product name, the default colour, the folder suffix, forecast steps possible, the zoom limit)
PRODUCTS = {
    'precip-global': ('precip-global', 'precip', 'Precipitation — radar, satellite and nowcast (Rainbow.ai)', 'dbz_u8', '-glob', True, 12),
    'precip':        ('precip', 'precip', 'Precipitation — radar and nowcast (Rainbow.ai)', 'dbz_u8', '-prec', True, 12),
    'radars':        ('radars', 'precip', 'Radar mosaic (Rainbow.ai)', 'dbz_u8', '-rad', False, 7),
    'type':          ('radars', 'type', 'Precipitation type — rain, snow, mixed (Rainbow.ai)', '0', '-type', False, 7),
    'clouds':        ('clouds', 'clouds', 'Clouds (Rainbow.ai)', '', '-clouds', False, 7),
}
PRESETS = {'world': (3, 0, 7, 0, 7), 'europe': (5, 14, 21, 8, 13), 'eu': (5, 14, 19, 8, 12)}       # name → zoom, x0, x1, y0, y1 (inclusive); europe: 22.5 W – 67.5 E, 21.9 – 66.5 N (48 tiles); eu: 22.5 W – 45 E, 32 – 66.5 N (30 tiles)
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
    for item in (spec or 'europe:5').split(','):
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
    def __init__(self, key, layer='radars', color='dbz_u8'):
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': UA, 'Ocp-Apim-Subscription-Key': key})
        self.layer = layer; self.color = color
        self.forecast = None                                                    # seconds, for the precip layers (None = the layer has no forecast axis)
        self.precip_type = False                                                # radars painted by type
        self.coverage = True                                                    # the coverage mask (dry but seen pixels opaque) — not for clouds
        self.requests = 0

    def product(self, token, color=None):
        """Set the client for one of PRODUCTS. → (kind, product name, folder suffix, forecast possible, zoom limit)."""
        layer, kind, name, default_color, suffix, forecast, zmax = PRODUCTS[token]
        self.layer = layer; self.forecast = 0 if forecast else None; self.precip_type = token == 'type'; self.coverage = kind != 'clouds'
        self.color = default_color if color is None else color
        return kind, name, suffix, forecast, zmax

    def snapshot(self):
        self.requests += 1
        r = self.s.get(API + '/snapshot', params={'layer': self.layer}, timeout=60)
        r.raise_for_status()
        return int(r.json()['snapshot'])

    def tile(self, snapshot, z, x, y):
        self.requests += 1
        path = '/%s/%d/%s%d/%d/%d' % (self.layer, snapshot, ('%d/' % self.forecast) if self.forecast is not None else '', z, x, y)   # the precip layers carry a forecast offset in seconds (0 = now)
        params = {}
        if self.coverage:
            params['coverage'] = 1
        if self.precip_type:
            params['use_precip_type'] = 1
        if self.color != '':
            params['color'] = self.color
        r = self.s.get(API + path, params=params, timeout=60)
        if r.status_code == 404:
            return None                                                     # no tile: nothing there (an ocean tile without radar, say)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert('RGBA')


def stitch(rb, snapshot, z, x0, x1, y0, y1, skip=None):
    """The area's tiles as one RGBA array (rows × cols of TILE pixels), plus how many tiles came back.
    skip(z, x, y) → True leaves a tile out (8 Nov: a tile wholly inside the open-data radars' reach is not fetched — the owner:
    «von Rainbow nur die Daten ziehen, die nicht als Open Source erhältlich sind»); the mosaic stays transparent there."""
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    size = None; got = 0; mosaic = None
    for j, y in enumerate(range(y0, y1 + 1)):
        for i, x in enumerate(range(x0, x1 + 1)):
            if skip is not None and skip(z, x, y):
                continue
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


def write_area(out, name, z, x0, x1, y0, y1, stamp, img, valid, keep, wet, note, kind='precip', folder=None):
    d = out + '/' + (folder or ('rb-' + name))
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
    json.dump({'product': note, 'kind': kind, 'name': ('World' if name == 'world' else name.capitalize()) + ' · Rainbow.ai' + ({'type': ' — precipitation type', 'clouds': ' — clouds'}.get(kind, '')), 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:3857',
               'zoom': z, 'tiles': {'x0': x0, 'x1': x1, 'y0': y0, 'y1': y1}, 'updated': now, 'source': SOURCE, 'licence': LICENCE,
               'legend': [{'from_mmh': (convert.SCALE[i - 1][0] if i else 0.1), 'to_mmh': (hi if hi < 1e8 else None), 'rgba': list(c)} for i, (hi, c) in enumerate(convert.SCALE)] if kind == 'precip' else [],
               'frames': [{'time': f[:4] + '-' + f[4:6] + '-' + f[6:8] + 'T' + f[8:10] + ':' + f[10:12] + 'Z', 'src': 'frames/' + f} for f in frames],
               'wet_share': round(wet, 4)}, open(d + '/frames.json', 'w'), indent=1)
    # the coverage edge from the transparent pixels, on a lon/lat grid a quarter (world) or a twentieth (a zoomed area) of a degree wide
    if kind != 'clouds' and convert.coverage_stale(d, stamp):
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


def frames_wanted(spec, cadence=30):
    """RAINBOW_FRAMES → the nowcast steps in minutes, 0 always first: «0,30,60» or «0-240/10», or «loop» = every RAINBOW_CADENCE_MIN up to +240
    (the owner, 7 Nov 20:42, Q40: the steps ahead at the loop's cadence); outside 0–240 or off the ten-minute grid → dropped."""
    out = {0}
    spec = (spec or '0').strip()
    if spec.lower() in ('loop', 'cadence'):
        st = max(10, int(round(cadence / 10.0)) * 10)
        return sorted(set(range(0, 241, st)))
    m = re.match(r'^(\d+)\s*-\s*(\d+)\s*/\s*(\d+)$', spec)
    if m:
        a, b, st = int(m.group(1)), int(m.group(2)), max(10, int(m.group(3)))
        out |= set(range(a, b + 1, st))
    else:
        for tok in spec.split(','):
            tok = tok.strip()
            if tok.isdigit():
                out.add(int(tok))
    return sorted(v for v in out if 0 <= v <= 240 and v % 10 == 0)


def usage_total(out, month):
    """The month's tiles over every Rainbow folder — the budget is one for all products and areas."""
    n = 0
    for d in os.listdir(out) if os.path.isdir(out) else []:
        if d.startswith('rb-'):
            u = usage_load(out + '/' + d)
            if u['month'] == month:
                n += u['tiles']
    return n


def write_nowcast(d, stamp, steps):
    """The steps beyond now of one run: nowcast/+<mmm>.png and nowcast.json; the previous run's files go."""
    nd = d + '/nowcast'
    os.makedirs(nd, exist_ok=True)
    keep = set()
    for lead, img in steps:
        f = '+%03d.png' % lead
        img.save(nd + '/' + f, optimize=True); keep.add(f)
    for f in os.listdir(nd):
        if f.endswith('.png') and f not in keep:
            os.remove(nd + '/' + f)
    json.dump({'issued': stamp.strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': SOURCE,
               'frames': [{'lead_min': lead, 'valid': (stamp + dt.timedelta(minutes=lead)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'src': 'nowcast/+%03d.png' % lead} for lead, _ in steps],
               'note': 'Rainbow.ai\'s nowcast of the issue named: the picture of each step as it stood then — a forecast, not an observation'}, open(d + '/nowcast.json', 'w'), indent=1)


def coverage_map(rb, out, name, z, x0, x1, y0, y1, snap, stamp):
    """Where ground radar reaches, as Rainbow's radars layer sees it: the coverage mask alone, grey on white — once a day, for the owner's question
    «wo gibt es keine Radarabdeckung»."""
    rb.product('radars'); rb.color = 'dbz_u8'
    mosaic, got = stitch(rb, snap, z, x0, x1, y0, y1)
    valid = mosaic[:, :, 3] > 0
    img = np.full(mosaic.shape[:2] + (4,), 255, dtype=np.uint8)
    img[valid] = (120, 130, 145, 255)
    d = out + '/rb-' + name
    os.makedirs(d, exist_ok=True)
    Image.fromarray(img, 'RGBA').save(d + '/coverage-map.png', optimize=True)
    W, S, E, N = area_bbox(z, x0, x1, y0, y1)
    json.dump({'at': stamp.strftime('%Y-%m-%dT%H:%M:%SZ'), 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:3857', 'zoom': z, 'covered_share': round(float(np.mean(valid)), 4), 'tiles': got,
               'note': 'grey = ground radar reaches (Rainbow.ai\'s radars layer, coverage mask); white = satellite estimates only (precip-global) or nothing'}, open(d + '/coverage-map.json', 'w'), indent=1)
    return got, float(np.mean(valid))


def coverage_map_stale(d):
    try:
        at = json.load(open(d + '/coverage-map.json'))['at']
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)) > dt.timedelta(hours=23)
    except Exception:
        return True


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
    """With a key: what each product answers — the snapshot, one tile of the world at zoom 2 (size, mode, the red channel's spread), the palettes."""
    for token in PRODUCTS:
        try:
            kind, name, suffix, forecast, zmax = rb.product(token)
            snap = rb.snapshot()
            log(token, '(' + rb.layer + ')', 'snapshot', snap, dt.datetime.fromtimestamp(snap, dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
            for color in (('dbz_u8', '0') if kind == 'precip' else (('0',) if kind == 'type' else ('',))):
                rb.color = color
                im = rb.tile(snap, 2, 1, 1)
                if im is None:
                    log('  ', token, 'color', repr(color), 'tile 2/1/1: 404'); continue
                a = np.asarray(im)
                log('  ', token, 'color', repr(color), 'tile 2/1/1:', im.size, im.mode, 'alpha>0 share %.3f' % float(np.mean(a[:, :, 3] > 0)), 'red min/max', int(a[:, :, 0].min()), int(a[:, :, 0].max()))
            if forecast:                                                        # one step ahead, to see the forecast axis answer
                rb.forecast = 3600; rb.color = 'dbz_u8'
                im = rb.tile(snap, 2, 1, 1)
                log('  ', token, '+60 min tile 2/1/1:', (im.size if im is not None else '404'))
        except Exception as e:
            log(token, 'failed:', e)
    log('requests spent by the probe:', rb.requests)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--probe', action='store_true', help='with a key: what the products answer, nothing written')
    ap.add_argument('--force', action='store_true', help='fetch even within the cadence (a look)')
    a = ap.parse_args()
    key = os.environ.get('RAINBOW_API_KEY', '').strip()
    spec = (os.environ.get('RAINBOW_LAYERS', '') or os.environ.get('RAINBOW_LAYER', '') or 'precip-global,type,clouds').strip().lower()
    tokens = []
    for t in spec.split(','):
        t = t.strip()
        if t and t not in tokens:
            if t in PRODUCTS:
                tokens.append(t)
            else:
                log('Rainbow.ai: unknown product', t, '— one of', ', '.join(PRODUCTS))
    color = (os.environ.get('RAINBOW_COLOR', '') or 'dbz_u8').strip()
    type_color = (os.environ.get('RAINBOW_TYPE_COLOR', '') or '0').strip()
    cadence = env_int('RAINBOW_CADENCE_MIN', 30)
    budget = env_int('RAINBOW_MONTHLY_TILES', 100000)
    steps = frames_wanted(os.environ.get('RAINBOW_FRAMES', ''), cadence)
    want_map = (os.environ.get('RAINBOW_COVERAGE_MAP', '') or '1').strip() not in ('0', 'no', 'off', 'false')
    areas = areas_wanted(os.environ.get('RAINBOW_AREAS', ''))
    if not key:
        log('Rainbow.ai: no key — skipped (the secret RAINBOW_API_KEY switches the source on)')
        return 0
    if not re.match(r'^(dbz_u8|\d)$', color):
        log('Rainbow.ai: unknown color', color, '— dbz_u8 or a palette number'); color = 'dbz_u8'
    if not re.match(r'^\d$', type_color):
        log('Rainbow.ai: unknown type color', type_color, '— a palette number'); type_color = '0'
    rb = Rainbow(key)
    if a.probe:
        probe(rb); return 0
    if not areas or not tokens:
        log('Rainbow.ai: nothing to fetch (areas: %s, products: %s)' % (areas, tokens)); return 0
    now = dt.datetime.now(dt.timezone.utc)
    month = now.strftime('%Y-%m')
    log('Rainbow.ai: products', ','.join(tokens), '· areas', ','.join('%s:%d' % (n, z) for n, z, *_ in areas), '· steps', steps, '· cadence', cadence, 'min · budget', budget, 'tiles/month · map', want_map)
    first_precip_done = set()
    od = None                                                                   # the open-data radars' reach (8 Nov): tiles wholly inside it are left out of the precipitation products
    if os.environ.get('RAINBOW_SKIP_OPEN', '1') != '0':
        try:
            import coverage
            od = coverage.open_data_mask(a.out)
            log('open-data reach:', 'none traced yet' if od is None else '%d cells of %.1f° — tiles wholly inside it are not fetched' % (int(od[0].sum()), od[2]))
        except Exception as e:
            log('open-data reach not read:', e)
    def skip_open(z, x, y):
        if od is None: return False
        return coverage.inside(od[0], tile_lon(x, z), tile_lat(y + 1, z), tile_lon(x + 1, z), tile_lat(y, z))
    for token in tokens:
        kind, pname, suffix, forecast, zmax = rb.product(token, color if kind_of(token) == 'precip' else (type_color if token == 'type' else ''))
        skip = skip_open if kind == 'precip' else None                           # the type and the clouds have no open-data twin
        try:
            snap = rb.snapshot()
        except Exception as e:
            log(token, 'snapshot failed:', e)
            for name, *_ in areas:
                status(a.out + '/rb-' + name + ('' if (kind == 'precip' and name not in first_precip_done) else suffix), False, 'snapshot: ' + str(e)[:200])
            continue
        stamp = dt.datetime.fromtimestamp(snap, dt.timezone.utc)
        for name, z, x0, x1, y0, y1 in areas:
            folder = 'rb-' + name + ('' if (kind == 'precip' and name not in first_precip_done) else suffix)   # the first precip product keeps the bare rb-<area> the boards know
            d = a.out + '/' + folder
            if z > zmax:
                log(folder, 'zoom', z, 'is beyond the product\'s', zmax, '— skipped'); status(d, False, 'zoom %d beyond the product\'s %d' % (z, zmax)); continue
            n_tiles = (x1 - x0 + 1) * (y1 - y0 + 1)
            n_skip = sum(1 for yy in range(y0, y1 + 1) for xx in range(x0, x1 + 1) if skip is not None and skip(z, xx, yy))   # inside the open-data reach (8 Nov)
            map_due = want_map and kind == 'precip' and name not in first_precip_done and coverage_map_stale(d)
            n_need = (n_tiles - n_skip) * ((len(steps) if forecast else 1)) + (n_tiles if map_due else 0)
            u = usage_load(d)
            if u['month'] != month:
                u = {'month': month, 'tiles': 0, 'runs': 0}
            u['tiles'] += 1                                                     # the snapshot call
            newest = newest_kept(d)
            if newest is not None and newest >= stamp:
                log(folder, 'kept picture', newest.strftime('%H:%MZ'), 'is the snapshot — nothing new'); usage_save(d, u)
                status(d, True, '', tiles_month=u['tiles'], budget=budget, cadence_min=cadence, note='no new snapshot')
                if kind == 'precip': first_precip_done.add(name)
                continue
            if not a.force and newest is not None and (stamp - newest) < dt.timedelta(minutes=max(1, cadence - 2)):
                log(folder, 'within the cadence (newest %s, snapshot %s, %d min)' % (newest.strftime('%H:%MZ'), stamp.strftime('%H:%MZ'), cadence)); usage_save(d, u)
                status(d, True, '', tiles_month=u['tiles'], budget=budget, cadence_min=cadence, note='within the cadence')
                if kind == 'precip': first_precip_done.add(name)
                continue
            total = usage_total(a.out, month)
            if total + n_need > budget:
                log(folder, 'monthly tile budget reached (%d + %d > %d over all products) — skipped until next month' % (total, n_need, budget)); usage_save(d, u)
                status(d, False, 'monthly tile budget reached (%d of %d over all products) — the runs skip until next month; raise RAINBOW_MONTHLY_TILES to go on' % (total, budget),
                       tiles_month=u['tiles'], budget=budget, cadence_min=cadence)
                if kind == 'precip': first_precip_done.add(name)
                continue
            before = rb.requests
            try:
                rb.forecast = 0 if forecast else None
                mosaic, got = stitch(rb, snap, z, x0, x1, y0, y1, skip)
                img, valid, wet = paint_mosaic(mosaic, rb.color)
                key_, kept = write_area(a.out, name, z, x0, x1, y0, y1, stamp, img, valid, a.frames, wet, pname + ' (zoom %d)' % z, kind=kind, folder=folder)
                ahead = []
                if forecast and len(steps) > 1:                                 # the nowcast steps beyond now
                    for lead in steps[1:]:
                        rb.forecast = lead * 60
                        m2, g2 = stitch(rb, snap, z, x0, x1, y0, y1, skip)
                        im2, _, _ = paint_mosaic(m2, rb.color)
                        ahead.append((lead, im2))
                    write_nowcast(d, stamp, ahead)
                if map_due:
                    g3, share = coverage_map(rb, a.out, name, z, x0, x1, y0, y1, snap, stamp)
                    log(folder, 'coverage map drawn: %d tiles, %.1f %% of the area within ground radar' % (g3, 100 * share))
                    rb.product(token, color if kind == 'precip' else (type_color if token == 'type' else ''))
                u['tiles'] += rb.requests - before; u['runs'] += 1
                usage_save(d, u)
                status(d, True, '', latest=stamp.strftime('%Y-%m-%dT%H:%MZ'), tiles_month=u['tiles'], budget=budget, cadence_min=cadence, tiles_run=rb.requests - before, kind=kind,
                       nowcast_steps=[l for l, _ in ahead], skipped_open=n_skip, note='%d of %d tiles came back' % (got, n_tiles) + (', %d left out inside the open-data radars\' reach' % n_skip if n_skip else ''))
                log(folder, 'done', key_, '— %d of %d tiles (%d left out inside the open-data reach), %d frames kept, %d steps ahead, wet share %.3f, %d tiles this month in this folder' % (got, n_tiles, n_skip, kept, len(ahead), wet, u['tiles']))
            except Exception as e:
                u['tiles'] += rb.requests - before; usage_save(d, u)
                log(folder, 'failed:', e)
                status(d, False, str(e)[:200], tiles_month=u['tiles'], budget=budget, cadence_min=cadence)
            if kind == 'precip':
                first_precip_done.add(name)
    log('Rainbow.ai: requests this run', rb.requests, '· month total', usage_total(a.out, month), 'of', budget)
    return 0


def kind_of(token):
    return PRODUCTS[token][1]


if __name__ == '__main__':
    sys.exit(main())
