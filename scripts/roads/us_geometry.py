"""Small WGS84 geometry operations for the offline US build; standard library only.

Core boxes are half open for selection. Geometry clipping includes boundaries so a
road crossing a box is retained even when neither source vertex is inside.
"""
import math

from roads import haversine_m

AREA_EPS = 1e-12  # square degrees: ignore floating point slivers at grid boundaries
GRID_DECIMALS = 8  # degrees: keep multiplication by .05 stable at negative longitudes
WORLD_DEG = 360  # degrees of longitude; unwrapped data boxes may cross the antimeridian
HALF_WORLD_DEG = 180
METRES_PER_DEG = haversine_m(0, 0, 1, 0)  # metres per latitude degree on roads.py's sphere
EDGE_BIN_DEG = .05  # latitude degrees: index state edges so each road does not scan an entire TIGER shape
CORE_DEG = .05  # degrees: frozen US selection cores (about 5 km in latitude)


def bounds(points):
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def pad_box(box, metres):
    w, s, e, n = box
    dy = metres / METRES_PER_DEG
    # Using the furthest latitude (including the padding) guarantees the margin
    # is at least this wide everywhere along the core, including Alaska.
    dx = metres / (METRES_PER_DEG * math.cos(math.radians(max(abs(s), abs(n)) + dy)))
    return [w - dx, s - dy, e + dx, n + dy]


def canonical_boxes(box):
    """Split a padded, unwrapped envelope into ordinary [-180, 180] request boxes."""
    w, s, e, n = box
    if w < -HALF_WORLD_DEG:
        return [[w + WORLD_DEG, s, HALF_WORLD_DEG, n], [-HALF_WORLD_DEG, s, e, n]]
    if e > HALF_WORLD_DEG:
        return [[w, s, HALF_WORLD_DEG, n], [-HALF_WORLD_DEG, s, e - WORLD_DEG, n]]
    return [list(box)]


def clip_ring(ring, box):
    """Sutherland-Hodgman clipping of a closed ring against a rectangle."""
    pts = [list(p[:2]) for p in ring]
    if pts and pts[0] == pts[-1]:
        pts.pop()
    for axis, edge, lower in ((0, box[0], True), (0, box[2], False),
                              (1, box[1], True), (1, box[3], False)):
        out = []
        for a, b in zip(pts[-1:] + pts[:-1], pts):
            ia, ib = (a[axis] >= edge, b[axis] >= edge) if lower else (a[axis] <= edge, b[axis] <= edge)
            if ia != ib:
                t = (edge - a[axis]) / (b[axis] - a[axis])
                p = [a[k] + t * (b[k] - a[k]) for k in (0, 1)]
                p[axis] = edge
                out.append(p)
            if ib:
                out.append(b)
        pts = out
    return pts + [pts[0]] if pts else []


def area(ring):
    # Translate first to avoid cancellation at US longitudes in tiny clipped cells.
    if not ring:
        return 0
    x, y = ring[0]
    return abs(sum((a[0] - x) * (b[1] - y) - (b[0] - x) * (a[1] - y)
                   for a, b in zip(ring, ring[1:]))) / 2


def polygons(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError("State coverage must be Polygon or MultiPolygon")


def geometry_boxes(geometry):
    return [bounds(poly[0]) for poly in polygons(geometry)]


def intersects_land(geometry, box):
    return any(overlaps(bounds(poly[0]), box) and
               area(clip_ring(poly[0], box)) - sum(area(clip_ring(h, box)) for h in poly[1:]) > AREA_EPS
               for poly in polygons(geometry))


def grid_box(i, j, step):
    return [round(i * step, GRID_DECIMALS), round(j * step, GRID_DECIMALS),
            round((i + 1) * step, GRID_DECIMALS), round((j + 1) * step, GRID_DECIMALS)]


def grid_range(low, high, step):
    # Rounded quotients prevent 40.05 / .05 becoming the next grid row.
    return range(math.floor(round(low / step, GRID_DECIMALS)), math.ceil(round(high / step, GRID_DECIMALS)))


def land_cells(geometry, step):
    """Rasterize polygon interiors and boundary cells, respecting holes and islands.

    Scanline edge bins avoid testing every grid square against every state vertex.
    A boundary edge through a cell's interior proves a nonzero land intersection;
    centre scanlines fill the rest. No cell scans the entire state polygon.
    """
    cells = set()
    for poly in polygons(geometry):
        rows, boundary = {}, set()
        for ring in poly:
            for a, b in zip(ring, ring[1:]):
                horizontal = a[1] == b[1]
                js = [math.floor(a[1] / step)] if horizontal else grid_range(min(a[1], b[1]), max(a[1], b[1]), step)
                for j in js:
                    if not horizontal:
                        rows.setdefault(j, []).append((a, b))
                    lo, hi = max(min(a[1], b[1]), j * step), min(max(a[1], b[1]), (j + 1) * step)
                    xs = [a[0], b[0]] if horizontal else [a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for y in (lo, hi)]
                    for i in grid_range(min(xs), max(xs), step):
                        box = grid_box(i, j, step)
                        for part in clip_line([a, b], box):
                            x, y = (part[0][0] + part[-1][0]) / 2, (part[0][1] + part[-1][1]) / 2
                            if box[0] < x < box[2] and box[1] < y < box[3]:
                                boundary.add((i, j))
                # Horizontal edges and vertical grid-aligned edges still bound
                # cells. Centre scanlines handle their land-facing interiors.
        for j, edges in rows.items():
            y = (j + .5) * step
            xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                        for a, b in edges if min(a[1], b[1]) <= y < max(a[1], b[1]))
            for left, right in zip(xs[::2], xs[1::2]):
                for i in grid_range(left, right, step):
                    if left <= (i + .5) * step < right:
                        cells.add((i, j))
        cells.update(boundary)
    return cells


def state_cells(state, step):
    """Use full-resolution frozen core ranges, or rasterize an unfrozen polygon."""
    if "core_rows" not in state:
        return land_cells(state["geometry"], step)
    if state["core_step"] != step:
        raise ValueError("Frozen state core step differs from the US build grid")
    return {(i, j) for j, start, stop in state["core_rows"] for i in range(start, stop)}


def covered_by_boxes(box, boxes):
    """Whether the *whole* core is covered by the union of metro selection boxes."""
    boxes = [b for b in boxes if overlaps(b, box)]
    if not boxes:
        return False
    xs = sorted({box[0], box[2]} | {max(box[0], b[0]) for b in boxes} | {min(box[2], b[2]) for b in boxes})
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((max(box[1], b[1]), min(box[3], b[3])) for b in boxes if b[0] <= left and b[2] >= right)
        end = box[1]
        for south, north in intervals:
            if south > end:
                break
            end = max(end, north)
        if end < box[3]:
            return False
    return True


def clip_line(line, box):
    """Liang-Barsky segment clipping, returning contiguous LineStrings."""
    parts, part = [], []
    for a, b in zip(line, line[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        low, high = 0., 1.
        for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]),
                     (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
            if p == 0:
                if q < 0:
                    high = -1
                    break
            elif p < 0:
                low = max(low, q / p)
            else:
                high = min(high, q / p)
        if low < high:
            start = [a[0] + low * dx, a[1] + low * dy]
            end = [a[0] + high * dx, a[1] + high * dy]
            if part and part[-1] != start:
                parts.append(part)
                part = []
            if not part:
                part.append(start)
            part.append(end)
        elif part:
            parts.append(part)
            part = []
    if part:
        parts.append(part)
    return parts


def canonical_lines(line):
    """Split a line crossing ±180 into ordinary, short WGS84 lines."""
    if not line:
        return []
    pts = [list(line[0][:2])]
    for point in line[1:]:
        lon, lat = point[:2]
        previous = pts[-1][0]
        lon += WORLD_DEG * round((previous - lon) / WORLD_DEG)
        pts.append([lon, lat])
    w, _, e, _ = bounds(pts)
    parts = []
    for shift in range(math.floor((w + HALF_WORLD_DEG) / WORLD_DEG),
                       math.floor((e + HALF_WORLD_DEG) / WORLD_DEG) + 1):
        for part in clip_line(pts, [-HALF_WORLD_DEG + shift * WORLD_DEG, -90,
                                     HALF_WORLD_DEG + shift * WORLD_DEG, 90]):
            parts.append([[x - shift * WORLD_DEG, y] for x, y in part])
    return parts


def clip_features(features, box):
    out = []
    for f in features:
        for line in canonical_lines(f["geometry"]["coordinates"]):
            for shift in (-WORLD_DEG, 0, WORLD_DEG):
                shifted = [[x + shift, y] for x, y in line]
                for part in clip_line(shifted, box):
                    out.append({"type": "Feature", "properties": dict(f["properties"]),
                                "geometry": {"type": "LineString", "coordinates": part}})
    return out


def in_ring(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def clip_to_geometry(features, geometry):
    return GeometryClipper(geometry).clip(features)


class GeometryClipper:
    """State polygon clipping with a latitude index, preserving holes and islands."""
    def __init__(self, geometry):
        self.parts = []
        for poly in polygons(geometry):
            rows = {}
            for ring in poly:
                for a, b in zip(ring, ring[1:]):
                    edge = tuple(a), tuple(b)
                    for row in range(math.floor(min(a[1], b[1]) / EDGE_BIN_DEG),
                                     math.floor(max(a[1], b[1]) / EDGE_BIN_DEG) + 1):
                        rows.setdefault(row, []).append(edge)
            self.parts.append((bounds(poly[0]), rows))

    @staticmethod
    def _inside(x, y, rows):
        edges = rows.get(math.floor(y / EDGE_BIN_DEG), ())
        inside = False
        for a, b in edges:
            if (min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1])
                    and abs((x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])) <= AREA_EPS):
                return True  # boundary roads intersect both adjoining states
            if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                inside = not inside
        return inside

    def clip(self, features):
        if not features:
            return []
        out = []
        for box, rows in self.parts:
            for f in clip_features(features, box):
                for a, b in zip(f["geometry"]["coordinates"], f["geometry"]["coordinates"][1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    ts, edges = {0., 1.}, set()
                    for row in range(math.floor(min(a[1], b[1]) / EDGE_BIN_DEG),
                                     math.floor(max(a[1], b[1]) / EDGE_BIN_DEG) + 1):
                        edges.update(rows.get(row, ()))
                    for c, d in edges:
                        ex, ey = d[0] - c[0], d[1] - c[1]
                        cross = dx * ey - dy * ex
                        if cross:
                            t = ((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / cross
                            u = ((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / cross
                            if 0 < t < 1 and 0 <= u <= 1:
                                ts.add(t)
                    ts = sorted(ts)
                    for low, high in zip(ts, ts[1:]):
                        x, y = a[0] + (low + high) / 2 * dx, a[1] + (low + high) / 2 * dy
                        if self._inside(x, y, rows):
                            out.append({"type": "Feature", "properties": dict(f["properties"]), "geometry": {
                                "type": "LineString", "coordinates": [[a[0] + t * dx, a[1] + t * dy] for t in (low, high)]}})
        return out
