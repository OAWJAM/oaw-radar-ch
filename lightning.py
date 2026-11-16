#!/usr/bin/env python3
"""EUMETSAT's Lightning Imager flashes as a source of this site (15 Nov 2026, the owner's «315 ja»): the MTG LI L2 Lightning Flashes
product (collection EO:EUM:DAT:0691, one BODY netCDF every ten minutes, ≈ 10 min behind real time) read through the EUMETSAT Data Store.

Nothing happens without the secrets EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET (Settings → Secrets and variables → Actions →
New repository secret; the pair comes from https://api.eumetsat.int/api-key/ after a free registration at https://user.eumetsat.int/):
the run logs «no key — skipped» and stays green. The search is open; the download needs a token (OAuth client credentials, an hour).

  python3 lightning.py [--out site] [--window 150] [--bbox -25,25,45,72] [--probe]

What a run does: lists the products of the last <window> + 20 minutes, fetches the BODY file of every product this site has no chunk
for yet, keeps the flashes inside the box (plain latitude/longitude, the European part of the disc) and writes

  li/chunks/<YYYYMMDDHHMM>.json   {from, to, n, flashes: [[lat, lon, s, events], …]}   — s = seconds after `from`, events = L1b events in the flash
  li/index.json                   {updated, product, source, licence, window_min, bbox, chunks: [{from, to, n, src}]}   — oldest first
  li/status.json                  {ok, error, at, latest, …}

The boards draw the flashes of the hour before each loop step, coloured by age. The folder carries no frames.json, so sources.json and
the coverage union never list it (it is not a radar). The secrets travel in the token request and the Authorization header alone.
"""
import argparse, base64, datetime as dt, io, json, os, re, sys, time
import requests
import convert

UA = convert.UA
COLLECTION = 'EO:EUM:DAT:0691'
SEARCH = 'https://api.eumetsat.int/data/search-products/1.0.0/os'
TOKEN = 'https://api.eumetsat.int/token'
EPOCH_2000 = 946684800                                                   # flash_time: seconds since 2000-01-01 00:00:00 (CF; satpy's li_l2_nc reader)
MAX_BYTES = 60 * 1024 * 1024                                             # a 10-min LFL body is ≈ 0.3 MB in a quiet hour, a few MB in a stormy one; more is a wrong file
MAX_FLASHES = 60000                                                      # per chunk, the newest kept — a board on a phone draws the hour's flashes as a few paths
SOURCE = {'product': 'MTG LI L2 Lightning Flashes (LFL), 10-min files', 'source': 'EUMETSAT', 'licence': 'EUMETSAT data policy — free of charge, Source: EUMETSAT (MTG Lightning Imager)',
          'collection': COLLECTION}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def token(key, secret):
    """The Data Store's OAuth token (client credentials): an hour's bearer token for the download links."""
    r = requests.post(TOKEN, data={'grant_type': 'client_credentials'}, auth=(key, secret), headers={'User-Agent': UA}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError('token: HTTP %d %s' % (r.status_code, (r.text or '')[:120].replace(key, '…').replace(secret, '…')))
    t = r.json().get('access_token', '')
    if not t:
        raise RuntimeError('token: no access_token in the answer')
    return t


def search(since, until, n=60):
    """The products between two times (open — no token): id, start, end and the BODY netCDF's download link."""
    p = {'format': 'json', 'pi': COLLECTION, 'dtstart': since.strftime('%Y-%m-%dT%H:%M:%SZ'), 'dtend': until.strftime('%Y-%m-%dT%H:%M:%SZ'), 'c': n, 'si': 0}
    r = requests.get(SEARCH, params=p, headers={'User-Agent': UA}, timeout=60)
    r.raise_for_status()
    out = []
    for f in r.json().get('features', []):
        pr = f.get('properties', {}) or {}
        m = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z?/(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z?', str(pr.get('date', '')))
        if not m:
            continue
        body = None
        for e in (pr.get('links', {}) or {}).get('sip-entries', []) or []:
            t = str(e.get('title', ''))
            if 'BODY' in t and t.endswith('.nc'):
                body = e.get('href'); break
        if body:
            out.append({'id': f.get('id', ''), 'from': m.group(1) + 'Z', 'to': m.group(2) + 'Z', 'body': body})
    out.sort(key=lambda x: x['from'])
    return out


def download(url, bearer):
    r = requests.get(url, headers={'User-Agent': UA, 'Authorization': 'Bearer ' + bearer}, timeout=120, stream=True)
    if r.status_code != 200:
        raise RuntimeError('download: HTTP %d' % r.status_code)
    buf = io.BytesIO()
    for part in r.iter_content(256 * 1024):
        buf.write(part)
        if buf.tell() > MAX_BYTES:
            raise RuntimeError('download: more than %d MB — not a flash file' % (MAX_BYTES // 1048576))
    return buf.getvalue()


def find_var(ds, name):
    """A variable by name at the root or in any group (the LFL body keeps them at the root; the older format had data/)."""
    if name in ds.variables:
        return ds.variables[name]
    for g in ds.groups.values():
        v = find_var(g, name)
        if v is not None:
            return v
    return None


def parse_flashes(blob, bbox, start):
    """The flashes of one BODY file inside the box: [[lat, lon, seconds after `start`, events], …] sorted by time, and what the file held."""
    import numpy as np
    import netCDF4
    ds = netCDF4.Dataset('inmemory.nc', mode='r', memory=blob)
    try:
        ds.set_auto_maskandscale(True)
        lat = find_var(ds, 'latitude'); lon = find_var(ds, 'longitude'); ft = find_var(ds, 'flash_time')
        if lat is None or lon is None or ft is None:
            names = sorted(ds.variables.keys()) + ['%s/%s' % (gn, vn) for gn, g in ds.groups.items() for vn in g.variables]
            raise RuntimeError('no latitude/longitude/flash_time — the file holds ' + ', '.join(names[:40]))
        la = np.ma.filled(np.asarray(lat[:], dtype='f8'), np.nan); lo = np.ma.filled(np.asarray(lon[:], dtype='f8'), np.nan)
        t = np.ma.filled(np.asarray(ft[:], dtype='f8'), np.nan)
        units = str(getattr(ft, 'units', '') or '')
        m = re.search(r'since\s+(\d{4})-(\d\d)-(\d\d)', units)
        epoch = EPOCH_2000
        if m:
            epoch = int(dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=dt.timezone.utc).timestamp())
        if 'millisecond' in units.lower() and 'since' in units.lower():
            t = t / 1000.0
        ev = find_var(ds, 'number_of_events')
        nev = np.ma.filled(np.asarray(ev[:], dtype='f8'), 0) if ev is not None and ev.shape == la.shape else np.zeros_like(la)
        W, S, E, N = bbox
        keep = np.isfinite(la) & np.isfinite(lo) & np.isfinite(t) & (la >= S) & (la <= N) & (lo >= W) & (lo <= E)
        total = int(la.size)
        la, lo, t, nev = la[keep], lo[keep], t[keep], nev[keep]
        secs = np.rint(t + epoch - start.timestamp()).astype('i8')
        order = np.argsort(secs, kind='stable')
        if order.size > MAX_FLASHES:
            order = order[-MAX_FLASHES:]
        rows = [[round(float(la[i]), 3), round(float(lo[i]), 3), int(secs[i]), int(nev[i])] for i in order]
        return rows, {'total': total, 'in_box': int(keep.sum()), 'units': units, 'vars': sorted(ds.variables.keys())[:16]}
    finally:
        ds.close()


def status(d, ok, error, **extra):
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    old = {}
    try:
        old = json.load(open(d + '/status.json'))
    except Exception:
        pass
    json.dump({'ok': ok, 'error': error, 'at': now, 'latest': old.get('latest', '')} | extra, open(d + '/status.json', 'w'), indent=1)


def write_index(d, bbox, window):
    W, S, E, N = bbox
    chunks = []
    for f in sorted(os.listdir(d + '/chunks')):
        if not re.match(r'^\d{12}\.json$', f):
            continue
        try:
            j = json.load(open(d + '/chunks/' + f))
            chunks.append({'from': j['from'], 'to': j['to'], 'n': int(j.get('n', 0)), 'src': 'chunks/' + f})
        except Exception:
            continue
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump({'updated': now, 'kind': 'lightning', 'window_min': window, 'hour_min': 60, 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:4326',
               'chunks': chunks} | SOURCE, open(d + '/index.json', 'w'), indent=1)
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--window', type=int, default=150, help='minutes of chunks kept (the boards need the loop\'s 90 plus the hour before each step)')
    ap.add_argument('--bbox', default='-25,25,45,72', help='W,S,E,N — the flashes kept')
    ap.add_argument('--probe', action='store_true', help='with the secrets: the newest product\'s entries and its file\'s variables, nothing written')
    a = ap.parse_args()
    key = os.environ.get('EUMETSAT_CONSUMER_KEY', '').strip(); secret = os.environ.get('EUMETSAT_CONSUMER_SECRET', '').strip()
    if not key or not secret:
        log('Lightning Imager: no key — skipped (the secrets EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET switch the source on)')
        return 0
    bbox = tuple(float(x) for x in a.bbox.split(','))
    d = a.out + '/li'; os.makedirs(d + '/chunks', exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    try:
        prods = search(now - dt.timedelta(minutes=a.window + 20), now + dt.timedelta(minutes=5))
    except Exception as e:
        log('Lightning Imager: search failed —', str(e)[:200]); status(d, False, 'search: ' + str(e)[:160]); return 0
    log('Lightning Imager: %d products in the last %d min' % (len(prods), a.window + 20), ('newest ' + prods[-1]['from'] + ' – ' + prods[-1]['to']) if prods else '')
    have = set(f[:12] for f in os.listdir(d + '/chunks') if re.match(r'^\d{12}\.json$', f))
    todo = [p for p in prods if re.sub(r'\D', '', p['from'])[:12] not in have]
    if a.probe:
        for p in prods[-3:]:
            log('  product', p['id'][-60:], p['from'], '→', p['to'])
    bearer = None
    done = 0; calls = 0; err = ''
    for p in (todo[-1:] if a.probe else todo):
        key12 = re.sub(r'\D', '', p['from'])[:12]
        try:
            if bearer is None:
                bearer = token(key, secret); calls += 1
            blob = download(p['body'], bearer); calls += 1
            start = dt.datetime.strptime(p['from'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
            rows, info = parse_flashes(blob, bbox, start)
            log('  %s: %d KB, %d flashes on the disc, %d in the box%s' % (key12, len(blob) // 1024, info['total'], info['in_box'], (' (vars: ' + ', '.join(info['vars']) + '; ' + info['units'] + ')') if done == 0 or a.probe else ''))
            if a.probe:
                continue
            json.dump({'from': p['from'], 'to': p['to'], 'n': len(rows), 'disc': info['total'], 'flashes': rows}, open(d + '/chunks/' + key12 + '.json.tmp', 'w'), separators=(',', ':'))
            os.replace(d + '/chunks/' + key12 + '.json.tmp', d + '/chunks/' + key12 + '.json')
            done += 1
        except Exception as e:
            err = key12 + ': ' + str(e)[:160]
            log('  %s failed — %s' % (key12, str(e)[:200].replace(key, '…').replace(secret, '…')))
            if 'token' in str(e) or 'HTTP 401' in str(e) or 'HTTP 403' in str(e):
                break
    if a.probe:
        return 0
    cutoff = (now - dt.timedelta(minutes=a.window)).strftime('%Y%m%d%H%M')
    for f in os.listdir(d + '/chunks'):
        if re.match(r'^\d{12}\.json$', f) and f[:12] < cutoff:
            os.remove(d + '/chunks/' + f)
    chunks = write_index(d, bbox, a.window)
    latest = chunks[-1]['to'] if chunks else ''
    status(d, err == '' or chunks != [], err, latest=latest, chunks=len(chunks), new=done, calls=calls, flashes_last=(chunks[-1]['n'] if chunks else 0))
    log('Lightning Imager: %d new chunk(s), %d kept (newest to %s), %d calls%s' % (done, len(chunks), latest or '—', calls, (' — ' + err) if err else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
