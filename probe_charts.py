#!/usr/bin/env python3
"""A look at the two chart sources the boards read (7 Nov) — by hand only, from the probe workflow, so their real answers stand in a run log:
ECMWF Open Charts (which run is current, whether the area and level names are accepted, the shape of the answer) and MET Norway's sigcharts
(the shape of available.json, one picture's size). Nothing is written to the site.

  python3 probe_charts.py
"""
import json, re, sys, time
import requests

UA = 'OAW-briefing-board/1.0 (+https://oaw.geofly.ch; ops desk aid)'
ECMWF = 'https://charts.ecmwf.int/opencharts-api/v1/'
METNO = 'https://api.met.no/weatherapi/sigcharts/2.0/'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def call(url, **params):
    r = requests.get(url, params=params, headers={'User-Agent': UA, 'Accept': 'application/json, */*'}, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = None
    return r.status_code, j, r


def ecmwf_product(product, **params):
    code, j, r = call(ECMWF + 'products/' + product + '/', **params)
    if code == 200 and isinstance(j, dict):
        d = (j.get('data') or {})
        log(' ', product, params or '(no params)', '→ 200', 'desc:', (d.get('attributes') or {}).get('description'), '| link:', str((d.get('link') or {}).get('href', ''))[:90])
    else:
        log(' ', product, params or '(no params)', '→', code, str(j)[:300] if j is not None else r.text[:200])
    return code, j


def main():
    log('ECMWF Open Charts — the product list')
    code, j, r = call(ECMWF + 'products/')
    names = [p.get('name') for p in (j or [])] if isinstance(j, list) else []
    log('  products:', code, len(names), [n for n in names if n in ('medium-mslp-wind850', 'medium-mslp-rain', 'medium-clouds', 'medium-uv-z', 'medium-t-z', 'medium-z500-t850', 'medium-uv-rh', 'medium-simulated-ir')])
    for product in ('medium-mslp-wind850', 'medium-mslp-rain', 'medium-clouds'):
        ecmwf_product(product)
        ecmwf_product(product, projection='opencharts_europe')
    ecmwf_product('medium-mslp-rain', projection='opencharts_central_europe')
    ecmwf_product('medium-mslp-wind850', projection='opencharts_north_west_europe')
    ecmwf_product('medium-mslp-wind850', projection='opencharts_nonsense')
    for product in ('medium-uv-z', 'medium-t-z'):
        ecmwf_product(product)
        ecmwf_product(product, projection='opencharts_europe', level=300)
        ecmwf_product(product, projection='opencharts_europe', level=250)
        ecmwf_product(product, projection='opencharts_europe', level=123)
    # a step of the current run: the base time from the default answer, then +12 h
    code, j = ecmwf_product('medium-mslp-wind850', projection='opencharts_europe')
    desc = str(((j or {}).get('data') or {}).get('attributes', {}).get('description', '')) if isinstance(j, dict) else ''
    m = re.search(r'Base time:\s*([A-Za-z]{3}\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{1,2})\s*UTC', desc)
    if m:
        import datetime as dt
        base = dt.datetime.strptime(re.sub(r'\s+', ' ', m.group(1)), '%a %d %b %Y %H').replace(tzinfo=dt.timezone.utc)
        for h in (3, 12, 48):
            ecmwf_product('medium-mslp-wind850', projection='opencharts_europe', base_time=base.strftime('%Y-%m-%dT%H:%M:%SZ'), valid_time=(base + dt.timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        ecmwf_product('medium-mslp-wind850', projection='opencharts_europe', base_time=base.strftime('%Y-%m-%dT%H:%M:%SZ'), valid_time=(base + dt.timedelta(hours=7)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        # the schema: which projections and levels the API lists for a product
        code, s, r = call(ECMWF + 'schema/', product='medium-uv-z', package='openchart')
        log('  schema medium-uv-z:', code, (json.dumps(s)[:1500] if s is not None else r.text[:300]))
    else:
        log('  no base time parsed from the description:', desc[:200])
    log('MET Norway sigcharts — available.json')
    code, j, r = call(METNO + 'available.json', area='nordic')
    log('  available.json nordic:', code, type(j).__name__, json.dumps(j)[:600] if j is not None else r.text[:300])
    code, j, r = call(METNO + 'available.json', area='norway')
    log('  available.json norway:', code, type(j).__name__, json.dumps(j)[:300] if j is not None else r.text[:200])
    r = requests.get(METNO, params={'area': 'nordic'}, headers={'User-Agent': UA}, timeout=60)
    log('  picture nordic (latest):', r.status_code, r.headers.get('Content-Type'), len(r.content), 'bytes', 'PNG' if r.content[:4] == b'\x89PNG' else 'not a PNG')
    return 0


if __name__ == '__main__':
    sys.exit(main())
