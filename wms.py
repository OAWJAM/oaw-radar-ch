#!/usr/bin/env python3
"""More radars through the same site — the national services that publish a WMS without a key (DWD for Germany, FMI for Finland): one
GetMap each in plain latitude/longitude (EPSG:4326) at the newest picture time the service names, kept as frames under <out>/<id>/,
and the list of every source of this site in <out>/sources.json (the Swiss converter's pictures stay at the root).

  python3 wms.py [--out site] [--frames 12] [--only de,fi] [--dry]

Sources (keyless, open data):
  DWD  https://maps.dwd.de/geoserver/dwd/wms            Datenlizenz Deutschland – Namensnennung 2.0 (dl-de/by-2-0)
  FMI  https://openwms.fmi.fi/geoserver/Radar/wms       CC BY 4.0
WMS 1.1.1 is asked for on purpose: its BBOX is lon,lat for EPSG:4326, whatever the server's axis order rules for 1.3.0.
"""
import argparse, datetime as dt, json, os, re, sys, time
import requests

UA = 'oaw-radar-ch converter (github actions; contact via the repository)'
SOURCES = [
    {'id': 'de', 'name': 'Germany', 'source': 'DWD', 'licence': 'dl-de/by-2-0, Source: DWD', 'product': 'RADOLAN',
     'url': 'https://maps.dwd.de/geoserver/dwd/wms', 'layers': ['dwd:Niederschlagsradar', 'dwd:RX-Produkt', 'dwd:RADOLAN-RV', 'dwd:RV-Produkt'],
     'bbox': (3.5, 47.0, 16.5, 55.5), 'step_min': 5},
    {'id': 'fi', 'name': 'Finland', 'source': 'FMI', 'licence': 'CC BY 4.0, Source: FMI', 'product': 'RR',
     'url': 'https://openwms.fmi.fi/geoserver/Radar/wms', 'layers': ['Radar:suomi_rr_eureffin', 'Radar:suomi_dbz_eureffin'],
     'bbox': (19.0, 59.0, 32.0, 70.5), 'step_min': 5},
]
ROOT_SOURCE = {'id': 'ch', 'name': 'Switzerland', 'path': '', 'licence': 'CC-BY, Source: MeteoSwiss', 'source': 'MeteoSwiss', 'product': 'RZC'}
STEP_DEG = 0.01            # a pixel per hundredth of a degree: 1 300 × 850 px for Germany


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def get(url, params, timeout=60):
    r = requests.get(url, params=params, headers={'User-Agent': UA}, timeout=timeout)
    r.raise_for_status()
    return r


def capabilities(url):
    """The capabilities document, or None."""
    try:
        return get(url, {'SERVICE': 'WMS', 'VERSION': '1.1.1', 'REQUEST': 'GetCapabilities'}, 60).text
    except Exception as e:
        log('capabilities not read:', e)
        return None


def layer_time(caps, layer):
    """The newest time the capabilities name for the layer (ISO 8601, 'Z'), or None: a <Dimension/Extent name="time"> with a default,
    a list of times, or a start/end/period range."""
    if not caps:
        return None
    m = re.search(r'<Layer[^>]*>\s*(?:<[^>]+>[^<]*</[^>]+>\s*)*?<Name>' + re.escape(layer) + r'</Name>(.*?)</Layer>', caps, re.S)
    body = m.group(1) if m else None
    if body is None:                                      # a layer nested deeper: the first block that names it
        i = caps.find('<Name>' + layer + '</Name>')
        if i < 0:
            return None
        body = caps[i:i + 20000]
    for tag in ('Extent', 'Dimension'):
        for dm in re.finditer(r'<' + tag + r'[^>]*name="time"([^>]*)>([^<]*)</' + tag + '>', body, re.I):
            attrs, text = dm.group(1), dm.group(2).strip()
            d = re.search(r'default="([^"]+)"', attrs)
            if d and re.match(r'^\d{4}-\d\d-\d\dT', d.group(1)):
                return d.group(1)
            if text:
                last = text.split(',')[-1].strip()
                if '/' in last:                            # start/end/period
                    end = last.split('/')[1]
                    if re.match(r'^\d{4}-\d\d-\d\dT', end):
                        return end
                elif re.match(r'^\d{4}-\d\d-\d\dT', last):
                    return last
    return None


def parse_time(s):
    s = s.strip().replace('Z', '+00:00')
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        t = dt.datetime.fromisoformat(s.split('.')[0] + '+00:00')
    return t.astimezone(dt.timezone.utc).replace(second=0, microsecond=0, tzinfo=dt.timezone.utc)


def getmap(src, layer, when, size):
    W, S, E, N = src['bbox']
    params = {'SERVICE': 'WMS', 'VERSION': '1.1.1', 'REQUEST': 'GetMap', 'LAYERS': layer, 'STYLES': '', 'SRS': 'EPSG:4326',
              'BBOX': '%s,%s,%s,%s' % (W, S, E, N), 'WIDTH': size[0], 'HEIGHT': size[1], 'FORMAT': 'image/png', 'TRANSPARENT': 'TRUE'}
    if when:
        params['TIME'] = when
    r = get(src['url'], params, 90)
    body = r.content
    if body[:4] != b'\x89PNG':
        exc = re.search(r'<ServiceException[^>]*>([^<]{1,300})', body.decode('utf-8', 'replace'))
        raise RuntimeError('not a picture: ' + (exc.group(1).strip() if exc else body[:120].decode('utf-8', 'replace')))
    return body


def write_frames(out, src, stamp, png, keep):
    d = out + '/' + src['id']
    os.makedirs(d + '/frames', exist_ok=True)
    key = stamp.strftime('%Y%m%d%H%M')
    with open(d + '/frames/' + key + '.png', 'wb') as f:
        f.write(png)
    with open(d + '/latest.png', 'wb') as f:
        f.write(png)
    frames = sorted(f for f in os.listdir(d + '/frames') if re.match(r'^\d{12}\.png$', f))
    for f in frames[:-keep] if len(frames) > keep else []:
        os.remove(d + '/frames/' + f)
    frames = frames[-keep:]
    W, S, E, N = src['bbox']
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump({'product': src['product'], 'bbox': {'west': W, 'south': S, 'east': E, 'north': N}, 'crs': 'EPSG:4326', 'updated': now, 'source': src['source'], 'licence': src['licence'],
               'frames': [{'time': f[:4] + '-' + f[4:6] + '-' + f[6:8] + 'T' + f[8:10] + ':' + f[10:12] + 'Z', 'src': 'frames/' + f} for f in frames]},
              open(d + '/frames.json', 'w'), indent=1)
    json.dump({'ok': True, 'error': '', 'at': now, 'latest': stamp.strftime('%Y-%m-%dT%H:%MZ')}, open(d + '/status.json', 'w'), indent=1)
    return key, len(frames)


def write_sources(out, done):
    """The list of this site's sources: the WMS ones that have frames, and the Swiss converter at the root."""
    lst = []
    for src in SOURCES:
        if os.path.exists(out + '/' + src['id'] + '/frames.json'):
            lst.append({'id': src['id'], 'name': src['name'], 'path': src['id'], 'licence': src['licence'], 'source': src['source'], 'product': src['product'], 'ok': src['id'] in done})
    if os.path.exists(out + '/frames.json'):
        lst.append(ROOT_SOURCE)
    json.dump({'updated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'sources': lst}, open(out + '/sources.json', 'w'), indent=1)
    return lst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--only', default=''); ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    only = [x for x in a.only.split(',') if x]
    done = set(); failed = []
    for src in SOURCES:
        if only and src['id'] not in only:
            continue
        W, S, E, N = src['bbox']
        size = (int(round((E - W) / STEP_DEG)), int(round((N - S) / STEP_DEG)))
        caps = None if a.dry else capabilities(src['url'])
        error = ''
        for layer in src['layers']:
            when = layer_time(caps, layer)
            stamp = parse_time(when) if when else dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
            if not when:                                        # no time named: the picture is the server's current one, stamped to the step
                stamp = stamp - dt.timedelta(minutes=stamp.minute % src['step_min'])
            log(src['id'], layer, 'time', when or '(current)', 'size', size)
            if a.dry:
                log('  would fetch', src['url'], 'TIME', when); done.add(src['id']); break
            key = stamp.strftime('%Y%m%d%H%M')
            if os.path.exists(a.out + '/' + src['id'] + '/frames/' + key + '.png'):
                log('  already there', key); done.add(src['id']); error = ''; break
            try:
                png = getmap(src, layer, when, size)
            except Exception as e:
                error = str(e); log('  failed:', error); continue
            k, n = write_frames(a.out, src, stamp, png, a.frames)
            log('  done', k, len(png), 'bytes, frames', n); done.add(src['id']); error = ''
            break
        if error:
            failed.append(src['id'] + ': ' + error)
            d = a.out + '/' + src['id']; os.makedirs(d, exist_ok=True)
            json.dump({'ok': False, 'error': error, 'at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}, open(d + '/status.json', 'w'), indent=1)
    if not a.dry:
        lst = write_sources(a.out, done)
        log('sources.json:', [s['id'] for s in lst])
    if failed:
        log('failed:', '; '.join(failed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
