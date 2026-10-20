"""Pure geometry (no CAD), so it can be tested without AutoCAD running."""
from __future__ import annotations

import math
from collections.abc import Sequence

Point2D = tuple[float, float]


def flat_to_points(coords: Sequence[float], dims: int = 2) -> list[Point2D]:
    """Convert AutoCAD's flat list (x1, y1, x2, y2, ...) into 2D points."""
    return [(float(coords[i]), float(coords[i + 1])) for i in range(0, len(coords) - dims + 1, dims)]


def signed_area(points: Sequence[Point2D]) -> float:
    n = len(points)
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def polygon_area(points: Sequence[Point2D]) -> float:
    """Area (always positive) using the shoelace formula."""
    if len(points) < 3:
        return 0.0
    return abs(signed_area(points))


def _bulge_arc(p1: Point2D, p2: Point2D, bulge: float) -> tuple[float, float]:
    """(radius, included angle) of a polyline arc segment."""
    chord = math.dist(p1, p2)
    theta = 4.0 * math.atan(abs(bulge))
    return chord / (2.0 * math.sin(theta / 2.0)), theta


def bulge_polygon_area(points: Sequence[Point2D], bulges: Sequence[float]) -> float:
    """Area of a closed polyline whose segments may be arcs (AutoCAD bulges)."""
    if len(points) < 2:
        return 0.0
    total = signed_area(points) if len(points) > 2 else 0.0
    n = len(points)
    for i, b in enumerate(bulges[:n]):
        if not b:
            continue
        p1, p2 = points[i], points[(i + 1) % n]
        if p1 == p2:
            continue
        r, theta = _bulge_arc(p1, p2, b)
        segment = r * r / 2.0 * (theta - math.sin(theta))
        total += math.copysign(segment, b)
    return abs(total)


def bulge_polyline_length(points: Sequence[Point2D], bulges: Sequence[float], closed: bool) -> float:
    n = len(points)
    last = n if closed else n - 1
    total = 0.0
    for i in range(max(last, 0)):
        p1, p2 = points[i], points[(i + 1) % n]
        b = bulges[i] if i < len(bulges) else 0.0
        if not b or p1 == p2:
            total += math.dist(p1, p2)
            continue
        r, theta = _bulge_arc(p1, p2, b)
        total += r * theta
    return total


def polygon_centroid(points: Sequence[Point2D]) -> Point2D:
    """Polygon centroid; falls back to the vertex average when the area is ~0."""
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    a = cx = cy = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-12:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    a *= 0.5
    return (cx / (6 * a), cy / (6 * a))


def point_in_polygon(pt: Point2D, polygon: Sequence[Point2D]) -> bool:
    """Ray casting. Points exactly on the edge may land on either side."""
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _scanline_crossings(polygon: Sequence[Point2D], y: float) -> list[float]:
    xs = []
    n = len(polygon)
    for i in range(n):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % n]
        if (y1 > y) == (y2 > y):
            continue
        xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return sorted(xs)


def interior_point(polygon: Sequence[Point2D]) -> Point2D:
    """A point inside the polygon: the centroid of an L or U shaped room can fall outside it,
    so then take the middle of the widest inside span over a few horizontal scanlines."""
    c = polygon_centroid(polygon)
    if len(polygon) < 3 or point_in_polygon(c, polygon):
        return c
    ys = [p[1] for p in polygon]
    lo, hi = min(ys), max(ys)
    best: tuple[float, Point2D] | None = None
    for k in range(1, 20):
        y = lo + (hi - lo) * k / 20
        xs = _scanline_crossings(polygon, y)
        for a, b in zip(xs[0::2], xs[1::2]):
            width = b - a
            if best is None or width > best[0]:
                best = (width, ((a + b) / 2, y))
    return best[1] if best else c


def polyline_length(points: Sequence[Point2D], closed: bool) -> float:
    return bulge_polyline_length(points, [], closed)


def _line_intersection(p1: Point2D, d1: Point2D, p2: Point2D, d2: Point2D) -> Point2D | None:
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-12:
        return None
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def offset_polyline(points: Sequence[Point2D], distance: float, closed: bool) -> list[Point2D]:
    """Straight-segment offset with mitered corners. Positive goes left of the drawing direction
    for open polylines and outward for closed ones, matching AutoCAD's "bigger" rule."""
    pts = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    if closed and len(pts) > 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 2:
        raise ValueError("At least 2 distinct points are needed to offset.")
    d = distance
    if closed and signed_area(pts) > 0:
        d = -distance
    n = len(pts)
    seg_count = n if closed else n - 1
    lines = []
    for i in range(seg_count):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        length = math.hypot(x2 - x1, y2 - y1)
        nx, ny = -(y2 - y1) / length, (x2 - x1) / length
        lines.append(((x1 + nx * d, y1 + ny * d), (x2 - x1, y2 - y1)))
    out: list[Point2D] = []
    for i in range(n):
        if not closed and i == 0:
            out.append(lines[0][0])
            continue
        if not closed and i == n - 1:
            (sx, sy), (dx, dy) = lines[-1]
            out.append((sx + dx, sy + dy))
            continue
        prev, cur = lines[i - 1], lines[i % seg_count]
        hit = _line_intersection(prev[0], prev[1], cur[0], cur[1])
        out.append(hit if hit is not None else cur[0])
    return out
