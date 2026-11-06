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
     'url': 'https://maps.dwd.de/geoserver/dwd/wms', 'layers': ['Radar_wn-analysis_1x1km_ger', 'dwd:Niederschlagsradar', 'dwd:RX-Produkt'],
     'bbox': (3.5, 47.0, 16.5, 55.5), 'step_min': 5, 'clear': True},
    {'id': 'fi', 'name': 'Finland', 'source': 'FMI', 'licence': 'CC BY 4.0, Source: FMI', 'product': 'RR',
     'url': 'https://openwms.fmi.fi/geoserver/Radar/wms', 'layers': ['radar_finland_cappi_rate', 'suomi_rr_eureffin', 'radar_finland_cappi_dbzh'],
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


def layer_times(caps, layer, n=12):
    """The newest n picture times the capabilities name for the layer (ISO, 'Z'): a list, or a start/end/period range expanded."""
    if not caps:
        return []
    i = caps.find('<Name>' + layer + '</Name>')
    if i < 0:
        return []
    body = caps[i:i + 20000]
    for tag in ('Extent', 'Dimension'):
        for dm in re.finditer(r'<' + tag + r'[^>]*name="time"([^>/]*)>([^<]*)</' + tag + '>', body, re.I):
            text = dm.group(2).strip()
            if not text:
                continue
            out = []
            for part in text.split(','):
                part = part.strip()
                if '/' in part:
                    bits = part.split('/')
                    if len(bits) >= 3 and re.match(r'^PT(\d+)M$', bits[2]) and re.match(r'^\d{4}-\d\d-\d\dT', bits[1]):
                        end = parse_time(bits[1]); step = int(re.match(r'^PT(\d+)M$', bits[2]).group(1))
                        start = parse_time(bits[0]) if re.match(r'^\d{4}-\d\d-\d\dT', bits[0]) else end - dt.timedelta(minutes=step * n)
                        t = end; k = 0
                        while t >= start and k < n:
                            out.append(t.strftime('%Y-%m-%dT%H:%M:%SZ')); t -= dt.timedelta(minutes=step); k += 1
                        out.reverse()
                    elif re.match(r'^\d{4}-\d\d-\d\dT', bits[-1 if len(bits) < 3 else 1]):
                        out.append(bits[1] if len(bits) >= 2 else bits[0])
                elif re.match(r'^\d{4}-\d\d-\d\dT', part):
                    out.append(part)
            if out:
                return sorted(set(out))[-n:]
    return []


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
    if src.get('clear'):
        body, reach = clear_background(body, True)
        src['_reach'] = reach
    return body


def clear_background(png, with_mask=False):
    """DWD paints the radar's reach as a half-transparent grey field with a magenta rim — on a chart that is a grey lid over Germany. The grey
    (r = g = b, not opaque) and the rim (magenta, and its blends with the grey) go transparent; the rain colours are opaque and stay."""
    import io
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert('RGBA'); a = np.array(im)
    r, g, b, al = (a[..., i].astype(int) for i in range(4))
    grey = (abs(r - g) < 4) & (abs(g - b) < 4) & (al < 255)
    rim = (abs(r - 252) < 8) & (g < 12) & (abs(b - 255) < 8)
    blend = (al < 232) & (r > 110) & (b > 110) & (g < r - 12) & (g < b - 12) & (abs(r - b) < 12)      # the rim's blends with the grey: purple-grey, never opaque
    white = (r > 245) & (g > 245) & (b > 245)
    reach = ((grey & (al > 0)) | rim | blend | ((al == 255) & ~white))                                 # the reach: the grey field (not the transparent outside, black too), its rim and the rain itself — never an opaque white outside (6 Nov, late — the coverage edge)
    if with_mask:
        n = a.shape[0] * a.shape[1]
        print('   clear_background: grey %.3f rim %.3f blend %.3f opaque %.3f transparent %.3f white %.3f reach %.3f corners %s' % (
            float(np.mean(grey & (al > 0))), float(np.mean(rim)), float(np.mean(blend)), float(np.mean(al == 255)), float(np.mean(al == 0)), float(np.mean(white & (al == 255))), float(np.mean(reach)),
            [tuple(int(x) for x in a[yy, xx]) for yy, xx in ((0, 0), (0, a.shape[1] - 1), (a.shape[0] - 1, 0), (a.shape[0] // 2, a.shape[1] // 2))]), flush=True)
    a[grey | rim | blend] = (0, 0, 0, 0)
    out = io.BytesIO(); Image.fromarray(a, 'RGBA').save(out, 'PNG', optimize=True)
    return (out.getvalue(), reach) if with_mask else out.getvalue()


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


NAMES = {'de': 'Germany', 'fi': 'Finland', 'se': 'Sweden', 'no': 'Norway', 'at': 'Austria', 'dk': 'Denmark', 'nl': 'Netherlands', 'fr': 'France', 'es': 'Spain', 'it': 'Italy', 'si': 'Slovenia', 'cz': 'Czechia'}


def write_sources(out, done):
    """The list of this site's sources: every folder with a frames.json (the WMS ones, the grids), the Swiss converter at the root last —
    the boards draw the sources in this order, so the Swiss picture lies on top where they overlap."""
    lst = []
    for d in sorted(os.listdir(out)):
        fj = out + '/' + d + '/frames.json'
        if not re.match(r'^[a-z0-9_-]{1,16}$', d) or not os.path.isfile(fj):
            continue
        try:
            j = json.load(open(fj))
        except Exception:
            continue
        st = {}
        try:
            st = json.load(open(out + '/' + d + '/status.json'))
        except Exception:
            pass
        lst.append({'id': d, 'name': NAMES.get(d, d.upper()), 'path': d, 'licence': j.get('licence', ''), 'source': j.get('source', ''), 'product': j.get('product', ''), 'ok': bool(st.get('ok', d in done)), 'updated': j.get('updated', '')})
    if os.path.exists(out + '/frames.json'):
        lst.append(ROOT_SOURCE)
    json.dump({'updated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'sources': lst}, open(out + '/sources.json', 'w'), indent=1)
    return lst


def probe(only):
    """For a look at a service: every layer of the capabilities that carries a time (name, default, the extent's tail) and the wanted layers' blocks."""
    for src in SOURCES:
        if only and src['id'] not in only:
            continue
        caps = capabilities(src['url'])
        if not caps:
            log(src['id'], 'no capabilities'); continue
        log(src['id'], 'capabilities', len(caps), 'chars; layers with a time dimension:')
        for m in re.finditer(r'<Name>([^<]+)</Name>(.{0,6000}?)(?=<Name>|</Layer>\s*</Layer>)', caps, re.S):
            name, body = m.group(1), m.group(2)
            t = re.search(r'<(Extent|Dimension)[^>]*name="time"([^>/]*)>([^<]*)</(?:Extent|Dimension)>', body, re.I)
            if t and re.search(r'radar|rx|rv|wn|rr|rate|dbz|precip|nieder', name, re.I):
                d = re.search(r'default="([^"]+)"', t.group(2)); ext = t.group(3).strip()
                log('   ', name, '| default', d.group(1) if d else '-', '| extent …', ext[-60:] if ext else '-')
        for layer in src['layers']:
            i = caps.find('<Name>' + layer + '</Name>')
            log('   wanted', layer, 'found' if i >= 0 else 'NOT in the capabilities', '| time:', layer_time(caps, layer))
            if i >= 0:
                log('   ', re.sub(r'\s+', ' ', caps[i:i + 700]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='site'); ap.add_argument('--frames', type=int, default=12)
    ap.add_argument('--only', default=''); ap.add_argument('--dry', action='store_true')
    ap.add_argument('--backfill', type=int, default=12, help='at most this many pictures fetched per source and pass (the missing ones, newest first)')
    ap.add_argument('--probe', action='store_true', help='print what the capabilities say about the layers (which exist, which carry a time) and stop')
    a = ap.parse_args()
    if a.probe:
        return probe([x for x in a.only.split(',') if x])
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
            times = layer_times(caps, layer, a.frames)
            if not times:                                       # no time named: the picture is the server's current one, stamped to the step
                now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
                times = [None]; stamps = [now - dt.timedelta(minutes=now.minute % src['step_min'])]
            else:
                stamps = [parse_time(t) for t in times]
            log(src['id'], layer, 'times', (times[-1] or '(current)') + (' and %d more' % (len(times) - 1) if len(times) > 1 else ''), 'size', size)
            if a.dry:
                log('  would fetch', src['url'], 'TIME', times[-1]); done.add(src['id']); break
            got = 0; tried = 0
            for when, stamp in reversed(list(zip(times, stamps))):   # newest first; at most --backfill pictures per run
                key = stamp.strftime('%Y%m%d%H%M')
                if os.path.exists(a.out + '/' + src['id'] + '/frames/' + key + '.png'):
                    got += 1; continue
                if tried >= max(1, a.backfill):
                    break
                tried += 1
                try:
                    png = getmap(src, layer, when, size)
                except Exception as e:
                    error = str(e); log('  failed', key, ':', error); break
                k, n = write_frames(a.out, src, stamp, png, a.frames)
                log('  done', k, len(png), 'bytes, frames', n); got += 1; error = ''
                if src.get('_reach') is not None:                                                        # the reach's edge from the newest picture fetched (6 Nov, late)
                    import convert
                    reach = src.pop('_reach')
                    if convert.coverage_stale(a.out + '/' + src['id'], stamp) or tried == 1:
                        convert.write_coverage(a.out + '/' + src['id'], reach, src['bbox'], STEP_DEG, src['source'])
            if got:
                done.add(src['id']); error = ''
                if tried == 0:
                    log('  all there already')
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
