#!/usr/bin/env python3
"""The radars' reach as one area (8 Nov, the owner: «kann man die Überlappungen verhindern, indem man einfach den Aussenrand darstellt?»).

Every precipitation source traces the edge of its own reach into <folder>/coverage.json (rings of [lon, lat]); drawn one over the
other the rings overlap where two countries' radars see the same sky. This module unites them: the rings of every precipitation
source are filled on one grid over Europe and the Atlantic (a tenth of a degree), the union's outline is traced, and the result is
written as coverage-union.json at the site's root. The boards draw that outline alone when it is there.

The same mask tells rainbow.py which tiles it may leave out: a tile that lies wholly inside the open-data radars' reach (Rainbow's own
folders left out of that mask) is not fetched — the owner (7 Nov 23:28): «von Rainbow nur die Daten ziehen, die nicht als Open Source
erhältlich sind».

    python3 coverage.py --out site            # writes site/coverage-union.json, prints what went in
"""
import argparse, json, os, sys
import numpy as np
from PIL import Image, ImageDraw

BBOX = (-40.0, 20.0, 60.0, 80.0)                                                # W S E N — the grid; a ring beyond it is clipped
STEP = 0.1                                                                       # degrees per cell: 1 000 × 600 cells
UNION_V = 1


def log(*a):
    print('[coverage]', *a, file=sys.stderr, flush=True)


def source_rings(out: str, with_rainbow: bool):
    """{folder: rings} of every precipitation source's coverage.json under the site (the root one is the Swiss source's).
    Rainbow's folders (rb-*) only when asked; the type and cloud folders never carry one that matters (the type one is the radars' too)."""
    found = {}
    cands = [''] + sorted(d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d)) and not d.startswith('_'))
    for d in cands:
        if d.startswith('rb-') and (not with_rainbow or d.endswith('-type') or d.endswith('-clouds')):
            continue
        p = os.path.join(out, d, 'coverage.json')
        if not os.path.isfile(p):
            continue
        try:
            j = json.load(open(p))
        except Exception as e:
            log(d or '(root)', 'coverage.json unreadable:', e); continue
        if j.get('union'):
            continue                                                             # never our own result
        rings = [r for r in (j.get('rings') or []) if isinstance(r, list) and len(r) >= 4]
        if rings:
            found[d or 'ch'] = rings
    return found


def rasterize(rings_by_source) -> np.ndarray:
    """The rings filled on the grid (holes filled with them: the owner wants the outer edge). → bool mask (rows north → south)."""
    W, S, E, N = BBOX
    nx, ny = int(round((E - W) / STEP)), int(round((N - S) / STEP))
    im = Image.new('L', (nx, ny), 0)
    dr = ImageDraw.Draw(im)
    for src, rings in rings_by_source.items():
        for ring in rings:
            pts = [((float(p[0]) - W) / STEP, (N - float(p[1])) / STEP) for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(pts) >= 3:
                dr.polygon(pts, fill=255)
    return np.asarray(im) > 127


def trace(mask: np.ndarray, min_len: int = 12, max_pts: int = 700):
    """The mask's 0.5 contour as rings of [lon, lat] — the outer edge, inner contours (holes) too when a hole is large enough to matter."""
    from skimage import measure
    W, S, E, N = BBOX
    pad = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=float); pad[1:-1, 1:-1] = mask
    rings = []
    for c in measure.find_contours(pad, 0.5):
        if len(c) < min_len:
            continue
        every = max(2, -(-len(c) // max_pts))                                    # the boards take 800 points a ring: thin the contour to fit (every 2nd point at least)
        pts = [[round(W + (col - 1 + 0.5) * STEP, 3), round(N - (row - 1 + 0.5) * STEP, 3)] for row, col in c[::every]]
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        rings.append(pts)
    rings.sort(key=len, reverse=True)
    return rings


def union_write(out: str):
    """coverage-union.json at the site's root: the outline of every precipitation source's reach together (Rainbow's radar mosaic among
    them when it runs). → the number of rings, 0 when nothing was there to unite."""
    srcs = source_rings(out, with_rainbow=True)
    p = os.path.join(out, 'coverage-union.json')
    if not srcs:
        try: os.remove(p)
        except FileNotFoundError: pass
        log('no source carries a coverage.json — no union'); return 0
    mask = rasterize(srcs)
    rings = trace(mask)
    W, S, E, N = BBOX
    json.dump({'v': UNION_V, 'union': True, 'sources': sorted(srcs), 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:4326', 'step_deg': STEP, 'rings': rings,
               'covered_cells': int(mask.sum()),
               'note': 'the outer edge of the area the listed sources\' radars see together (rain or none), traced at a tenth of a degree from their coverage.json files'},
              open(p + '.tmp', 'w'))
    os.replace(p + '.tmp', p)
    log('union of', ', '.join(sorted(srcs)), '→', len(rings), 'rings,', int(mask.sum()), 'cells')
    return len(rings)


def open_data_mask(out: str):
    """The open-data radars' reach alone (no Rainbow folder) as (mask, bbox, step), or None when no source has traced its reach yet."""
    srcs = source_rings(out, with_rainbow=False)
    if not srcs:
        return None
    return rasterize(srcs), BBOX, STEP


def inside(mask, lon_w, lat_s, lon_e, lat_n, n: int = 6) -> bool:
    """Is the box lon_w…lon_e × lat_s…lat_n wholly inside the mask? Sampled on an n × n lattice; a box beyond the grid is never inside."""
    W, S, E, N = BBOX
    if lon_w < W or lon_e > E or lat_s < S or lat_n > N:
        return False
    for i in range(n):
        for j in range(n):
            lon = lon_w + (lon_e - lon_w) * (i + 0.5) / n
            lat = lat_s + (lat_n - lat_s) * (j + 0.5) / n
            col = int((lon - W) / STEP); row = int((N - lat) / STEP)
            if row < 0 or col < 0 or row >= mask.shape[0] or col >= mask.shape[1] or not mask[row, col]:
                return False
    return True


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='site')
    a = ap.parse_args()
    n = union_write(a.out)
    print('rings:', n)
