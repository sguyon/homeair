"""BTS file boundary: synthetic zipped OpenFileGDB, never the national download."""
import builtins
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest

pytest.importorskip('pyogrio')

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('roads_hpms_build', ROOT / 'scripts/roads/build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
import hpms

FILE = ROOT / 'tests/fixtures/roads/hpms2024.zip'
BOXES = [[-74.01, 39.99, -73.98, 40.01], [-74.005, 39.995, -73.985, 40.005]]


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(hpms, 'HPMS_FILE_BYTES', FILE.stat().st_size, raising=False)
    monkeypatch.setattr(hpms, 'HPMS_FILE_SHA256', hashlib.sha256(FILE.read_bytes()).hexdigest(), raising=False)
    return FILE


def test_file_reader_selects_boxes_columns_and_maps_rows_for_both_joins(tmp_path, monkeypatch, pinned):
    import pyogrio.raw
    read, calls = pyogrio.raw.read, []

    def capture(path, **kwargs):
        calls.append((path, kwargs))
        return read(path, **kwargs)

    monkeypatch.setattr(pyogrio.raw, 'read', capture)
    # Neither service cache shape can masquerade as the BTS source.
    (tmp_path / 'hpms-NY.json').write_text(json.dumps({'features': [{}], 'version': 5}))
    (tmp_path / 'hpms-NY-2025-old.json').write_text(json.dumps({'features': [{}]}))
    rows = hpms.fetch_state('NY', BOXES, tmp_path, pinned)
    assert [r['properties']['objectid'] for r in rows] == [1, 2, 3]
    p = rows[0]['properties']
    assert p == {'objectid': 1, 'route_name': 'I9', 'route_number': '9', 'route_signing': 2,
                 'aadt': 30000, 'aadt_combination': 1200, 'aadt_single_unit': None,
                 'f_system': 1, 'facility_type': 1}
    assert rows[1]['properties']['route_number'] is None
    assert all(len(point) == 2 for r in rows for line in hpms._lines(r['geometry']) for point in line)
    assert len(calls) == 2
    assert all(path == '/vsizip/' + str(pinned) + '/HPMS2024.gdb' for path, _ in calls)
    assert {tuple(kw['bbox']) for _, kw in calls} == {tuple(box) for box in BOXES}
    assert all(kw['layer'] == 'HPMS_FULL_NY_2024' and kw['where'] == 'aadt > 0'
               and set(kw['columns']) == {'route_id', 'routename', 'routenumber', 'routesigning', 'aadt',
                   'aadt_combination', 'aadt_single_unit', 'f_system', 'facility_type'} for _, kw in calls)
    report = hpms.fetch_report('NY', BOXES, tmp_path)
    assert report == {'pages': 2, 'rows': 3, 'expected_query_rows': 6, 'status': 'ok',
                      'source': hpms.HPMS_SOURCE, 'year': 2024}
    cache = hpms.state_cache('NY', BOXES, tmp_path)
    assert 'bts' in cache.name and '2024' in cache.name
    assert cache == hpms.state_cache('NY', list(reversed(BOXES)), tmp_path)
    assert cache != hpms.state_cache('NY', BOXES[:1], tmp_path)
    monkeypatch.setattr(pyogrio.raw, 'read', lambda *a, **k: pytest.fail('cache missed'))
    assert hpms.fetch_state('NY', BOXES, tmp_path, pinned) == rows
    # The metro join can consume the normalized rows; ramps remain excluded.
    features = [{'properties': {'g': 'road'}, 'geometry': rows[0]['geometry']}]
    # The join consumes LineStrings from OSM, while the GDB returns MultiLineStrings.
    features[0]['geometry'] = {'type': 'LineString', 'coordinates': hpms._lines(rows[0]['geometry'])[0]}
    road = {'name': 'I-9', 'refs': ['I 9'], 'aadt': None, 'trucks_pct': None}
    assert hpms.join({'road': road}, features, rows) == 1
    assert road['aadt'] == 30000 and road['trucks_pct'] == 4


def test_metro_uses_same_file_reader(tmp_path, pinned):
    rows = hpms.fetch('NY', BOXES[0], tmp_path, pinned)
    assert [r['properties']['objectid'] for r in rows] == [1, 2, 3]


@pytest.mark.parametrize('route_id,route_name,facility,expected', [
    ('LA_LA_E WASHINGTON BLVD_P', None, 2, 'east washington boulevard'),  # CA object 187543
    ('LA_LA_S VERMONT AVE_P', '', 2, 'south vermont avenue'),
    ('SBD_HIGH_CUNNINGHAM ST_P', '  ', 2, 'cunningham street'),
    ('LA_LA_N BROADWAY_P', None, 2, 'north broadway'),  # CA object 197853
    ('LA_LA_AVE OF THE STARS_P', None, 2, 'avenue of the stars'),
    ('LA_SMCA_CALIFORNIA INCLINE_P', None, 2, 'california incline'),
    ('LA_SCTA_VIA PRINCESSA_P', None, 2, 'via princessa'),
    ('RAMP_117032_P', None, 2, None),
    ('RAMP_E WASHINGTON BLVD_P', None, 2, None),
    ('RAMP_LA_E WASHINGTON BLVD_P', None, 2, None),
    ('LA_LA_E WASHINGTON BLVD_P', None, 4, None),
    ('SHS_101._P', None, 2, None),
    ('LA_LA_123456_P', None, 2, None),
    ('LA_LA_AB123 ROAD_P', None, 2, None),
    ('LA_LA_ROAD 123456_P', None, 2, None),
    ('LA_LA_E WASHINGTON BLVD_P', 'Different Road', 2, 'Different Road'),
])
def test_california_route_id_fallback_only_accepts_street_names(monkeypatch, pinned, route_id, route_name, facility, expected):
    import numpy as np
    import pyogrio.raw
    read = pyogrio.raw.read

    def california_row(path, **kwargs):
        # Real GDB/WKB boundary; identifiers and count have CA 187543's shape.
        meta, ids, geoms, arrays = read(path, **{**kwargs, 'layer': 'HPMS_FULL_NY_2024'})
        fields = dict(zip(meta['fields'], arrays))
        if 'route_id' in kwargs['columns']:
            fields['route_id'] = np.array([route_id], dtype=object)
        fields.update(routename=np.array([route_name], dtype=object),
                      routenumber=np.array([np.nan]), routesigning=np.array([np.nan]),
                      facility_type=np.array([facility]), aadt=np.array([27652]))
        meta = {**meta, 'fields': list(fields)}
        return meta, np.array([187543]), geoms[:1], [f[:1] for f in fields.values()]

    monkeypatch.setattr(pyogrio.raw, 'read', california_row)
    monkeypatch.setattr(hpms, 'load_states', lambda: {'CA': {'geometry': {'type': 'Polygon', 'coordinates':
        [[[-75, 39], [-73, 39], [-73, 41], [-75, 41], [-75, 39]]]}}})
    row = hpms._read_box(pinned, 'CA', BOXES[0])[0]
    assert row['properties']['route_name'] == expected
    other = hpms._read_box(pinned, 'AZ', BOXES[0])[0]
    assert other['properties']['route_name'] == (route_name if route_name and route_name.strip() else None)
    matches = expected is not None and expected != 'Different Road'
    road = {'name': expected if matches else 'East Washington Boulevard', 'refs': []}
    features = [{'properties': {'g': 'road'}, 'geometry':
                 {'type': 'LineString', 'coordinates': [[-74, 40], [-73.99, 40]]}}]
    assert hpms.join_by_state({'road': road}, features, {'CA': [row]})['CA']['joined_groups'] == int(matches)
    # A name fallback supplies identity only; a crossing or a nearby parallel line cannot supply support.
    for coordinates in ([[-73.995, 39.995], [-73.995, 40.005]],
                        [[-74, 40.0002], [-73.99, 40.0002]]):
        features[0]['geometry']['coordinates'] = coordinates
        assert hpms.join_by_state({'road': road}, features, {'CA': [row]})['CA']['joined_groups'] == 0


@pytest.mark.parametrize('change,error', [('size', 'size'), ('digest', 'sha256')])
def test_pin_failure_precedes_read_and_cache_reuse(tmp_path, monkeypatch, pinned, change, error):
    hpms.fetch_state('NY', BOXES, tmp_path, pinned)
    monkeypatch.setattr(hpms, 'HPMS_FILE_BYTES' if change == 'size' else 'HPMS_FILE_SHA256',
                        1 if change == 'size' else '0' * 64)
    with pytest.raises(ValueError, match=error):
        hpms.fetch_state('NY', BOXES, tmp_path, pinned)


def test_missing_package_and_layer_leave_error_report_without_success_cache(tmp_path, monkeypatch, pinned):
    original = builtins.__import__

    def without_pyogrio(name, *a, **kw):
        if name.startswith('pyogrio'):
            raise ImportError('not installed')
        return original(name, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(builtins, '__import__', without_pyogrio)
        with pytest.raises(RuntimeError, match='pyogrio'):
            hpms.fetch_state('NY', BOXES, tmp_path, pinned)
    assert hpms.fetch_report('NY', BOXES, tmp_path)['status'] == 'error'
    assert not hpms.state_cache('NY', BOXES, tmp_path).exists()
    with pytest.raises(RuntimeError, match='HPMS NJ'):
        hpms.fetch_state('NJ', BOXES, tmp_path, pinned)
    assert not hpms.state_cache('NJ', BOXES, tmp_path).exists()
    assert hpms.fetch_state('NY', BOXES, tmp_path, pinned)
    assert not hpms.state_cache('NY', BOXES, tmp_path).with_suffix('.error.json').exists()


def test_measured_coordinates_are_dropped(tmp_path, monkeypatch, pinned):
    import pyogrio.raw
    read = pyogrio.raw.read

    def measured(*a, **kw):
        meta, ids, geoms, fields = read(*a, **kw)
        geoms[0] = struct.pack('<BII6d', 1, 2002, 2, -74, 40, 123, -73.99, 40, 456)
        return meta, ids, geoms, fields

    monkeypatch.setattr(pyogrio.raw, 'read', measured)
    row = hpms.fetch_state('NY', BOXES[:1], tmp_path, pinned)[0]
    assert row['geometry']['coordinates'] == [[-74, 40], [-73.99, 40]]


@pytest.mark.parametrize('metro', ['nyc', 'us'])
def test_cli_requires_hpms_file_before_processing_input(tmp_path, monkeypatch, capsys, metro):
    monkeypatch.setattr(build, 'run_osmium', lambda *a, **kw: pytest.fail('read input before HPMS argument check'))
    with pytest.raises(SystemExit) as error:
        build.main([metro, 'input.pbf', '--out', str(tmp_path)])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert '--hpms-file' in message
    assert 'https://geodata.bts.gov/datasets/5e6a977c2d7c4ec1bdc82e684d3384f2/about' in message


@pytest.mark.parametrize('metro', ['nyc', 'us'])
def test_cli_wrong_hpms_path_is_a_plain_error_before_processing_input(tmp_path, monkeypatch, capsys, metro):
    monkeypatch.setattr(build, 'run_osmium', lambda *a, **kw: pytest.fail('read input before HPMS file check'))
    missing = tmp_path / 'missing.zip'
    with pytest.raises(SystemExit) as error:
        build.main([metro, 'input.pbf', '--out', str(tmp_path), '--work', str(tmp_path),
                    '--hpms-file', str(missing)])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert str(missing) in message and 'HPMS' in message
    assert 'Traceback' not in message


@pytest.mark.parametrize('metro,counts', [('nyc', True), ('nyc', False), ('paris', True)])
def test_metro_cli_reads_bts_counts_only_when_needed(tmp_path, monkeypatch, pinned, metro, counts):
    meta = {**build.metro('nyc'), 'id': metro, 'box': BOXES[0],
            'country': 'US' if metro == 'nyc' else 'FR', 'hpms_states': ['NY'] if metro == 'nyc' else []}
    monkeypatch.setattr(build, 'metro', lambda _: meta)
    source = tmp_path / 'osm.jsonseq'
    source.write_text(json.dumps({'properties': {'highway': 'motorway', 'name': 'I-9', 'ref': 'I 9'},
                                 'geometry': {'type': 'LineString', 'coordinates': [[-74, 40], [-73.99, 40]]}}))
    monkeypatch.setattr(build, 'run_osmium', lambda *a, **kw: source)
    args = [metro, 'input.pbf', '--out', str(tmp_path / 'out'), '--osm-date', '2026-11-21', '--no-speeds']
    if metro == 'nyc' and counts:
        args += ['--hpms-file', str(pinned)]
    if not counts:
        args += ['--no-counts']
    assert build.main(args) == 0
    import gzip
    index = json.loads((tmp_path / 'out/index.json').read_text())
    doc = json.loads(gzip.decompress((tmp_path / 'out' / index['metros'][0]['file']).read_bytes()))
    road = next(iter(doc['roads'].values()))
    assert road['aadt'] == (30000 if metro == 'nyc' and counts else None)


def test_national_file_doubles_become_whole_route_numbers(monkeypatch, pinned):
    # The BTS file stores routenumber, routesigning and the codes as doubles (278.0); str(278.0) made ".0" a route
    # suffix, so every numbered row became a variant that never matches its mainline.
    import numpy as np
    import pyogrio.raw
    read = pyogrio.raw.read

    def as_doubles(*args, **kwargs):
        meta, ids, geometries, fields = read(*args, **kwargs)
        doubles = ('routenumber', 'routesigning', 'f_system', 'facility_type')   # as the national file stores them
        return meta, ids, geometries, [np.array([np.nan if v is None or v == '' else float(v) for v in f])
                                       if name in doubles else f for name, f in zip(meta['fields'], fields)]

    monkeypatch.setattr(pyogrio.raw, 'read', as_doubles)
    rows = hpms._read_box(pinned, 'NY', BOXES[0])
    numbered = [r['properties'] for r in rows if r['properties'].get('route_number') is not None]
    assert numbered
    for p in numbered:
        assert type(p['route_number']) is int and type(p['route_signing']) is int
    segment = hpms._prepare([rows[rows.index(next(r for r in rows if r['properties'].get('route_number')))]])[0]
    assert len(segment['route']) == 2
