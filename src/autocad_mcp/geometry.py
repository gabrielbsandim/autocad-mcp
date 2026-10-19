"""Pure geometry (no COM), so it can be tested without AutoCAD running."""
from __future__ import annotations

from collections.abc import Sequence

Point2D = tuple[float, float]


def flat_to_points(coords: Sequence[float], dims: int = 2) -> list[Point2D]:
    """Convert AutoCAD's flat list (x1, y1, x2, y2, ...) into 2D points."""
    return [(float(coords[i]), float(coords[i + 1])) for i in range(0, len(coords) - dims + 1, dims)]


def polygon_area(points: Sequence[Point2D]) -> float:
    """Area (always positive) using the shoelace formula."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


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


def polyline_length(points: Sequence[Point2D], closed: bool) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += ((points[i + 1][0] - points[i][0]) ** 2 + (points[i + 1][1] - points[i][1]) ** 2) ** 0.5
    if closed and len(points) > 2:
        total += ((points[0][0] - points[-1][0]) ** 2 + (points[0][1] - points[-1][1]) ** 2) ** 0.5
    return total
