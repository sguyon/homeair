"""Regenerate hpms2024.zip (synthetic, never copied from the national archive).

Run with the packages in requirements-dev.txt:
    python tests/fixtures/roads/make_hpms_gdb.py [output.zip]
"""
import argparse
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from pyogrio.raw import write
from shapely import to_wkb
from shapely.geometry import MultiLineString


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', type=Path, default=Path(__file__).with_name('hpms2024.zip'))
    args = parser.parse_args(argv)
    # Four coincident rows and one far away exercise bbox/count/ramp filters.
    near = MultiLineString([[(-74, 40), (-73.99, 40)]])
    far = MultiLineString([[(-72, 42), (-71.99, 42)]])
    fields = {
        'route_id': np.array(['TEST_I9', 'TEST_4TH', 'TEST_RAMP', 'TEST_ZERO', 'TEST_FAR'], dtype=object),
        'routename': np.array(['I9', '4TH AVE', 'Ramp', 'No count', 'Far'], dtype=object),
        'routenumber': np.array(['9', None, '9', '9', '9'], dtype=object),
        'routesigning': np.array([2, np.nan, 2, 2, 2], dtype=float),
        'aadt': np.array([30000, 15000, 5000, 0, 90000], dtype=float),
        'aadt_combination': np.array([1200, np.nan, 10, np.nan, np.nan], dtype=float),
        'aadt_single_unit': np.array([np.nan, np.nan, 20, np.nan, np.nan], dtype=float),
        'f_system': np.array([1, 3, 1, 1, 1], dtype=float),
        'facility_type': np.array([1, 1, 4, 1, 1], dtype=float),
        'unused': np.array(['ignore'] * 5, dtype=object),
    }
    with tempfile.TemporaryDirectory() as folder:
        gdb = Path(folder) / 'HPMS2024.gdb'
        write(gdb, geometry=to_wkb([near, near, near, near, far]),
              field_data=list(fields.values()), fields=list(fields),
              driver='OpenFileGDB', layer='HPMS_FULL_NY_2024',
              geometry_type='MultiLineString', crs='EPSG:4326')
        with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(gdb.iterdir()):
                if path.is_file():
                    member = zipfile.ZipInfo(f'HPMS2024.gdb/{path.name}')
                    member.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(member, path.read_bytes())
    print(f'{args.output}: 5 synthetic rows')


if __name__ == '__main__':
    main()
