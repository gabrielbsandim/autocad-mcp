import pytest

from autocad_mcp.geometry import flat_to_points, point_in_polygon, polygon_area, polygon_centroid, polyline_length
from autocad_mcp.layer_standard import DEFAULT_STANDARD, load_standard, plan, target_for
from autocad_mcp.units import area_to_m2, fmt_num, meters_per_unit

SQUARE = [(0, 0), (4, 0), (4, 3), (0, 3)]


def test_area_and_centroid():
    assert polygon_area(SQUARE) == 12
    assert polygon_centroid(SQUARE) == pytest.approx((2, 1.5))
    assert polygon_area(list(reversed(SQUARE))) == 12


def test_point_in_polygon():
    assert point_in_polygon((1, 1), SQUARE)
    assert not point_in_polygon((5, 1), SQUARE)
    l_shape = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]
    assert point_in_polygon((0.5, 3), l_shape)
    assert not point_in_polygon((3, 3), l_shape)


def test_flat_points_and_length():
    assert flat_to_points([0, 0, 3, 4]) == [(0, 0), (3, 4)]
    assert flat_to_points([0, 0, 0, 3, 4, 0], 3) == [(0, 0), (3, 4)]
    assert polyline_length(SQUARE, closed=True) == 14


def test_units():
    assert meters_per_unit(5)[0] == 0.01
    assert meters_per_unit(0)[0] is None
    assert meters_per_unit(0, "cm")[0] == 0.01
    with pytest.raises(ValueError):
        meters_per_unit(4, "in")
    assert area_to_m2(120000, 0.01) == pytest.approx(12)
    assert fmt_num(1234.5) == "1,234.50"
    assert fmt_num(None) == "-"


def test_layer_standard_matching():
    rules = load_standard(DEFAULT_STANDARD)
    assert target_for("walls", rules).name == "A-WALL"
    assert target_for("WALL-BEYOND", rules).name == "A-WALL-BEYOND"
    assert target_for("doors", rules).name == "A-DOOR"
    assert target_for("0", rules) is None
    assert target_for("XYZ", rules) is None


def test_layer_plan_merges_duplicates():
    rules = load_standard(DEFAULT_STANDARD)
    p = plan(["0", "DOORS", "DOOR_OLD", "A-ANNO-DIMS", "DIMS", "Junk"], rules)
    assert ("DOORS", "A-DOOR") in p["rename"]
    assert ("DOOR_OLD", "A-DOOR") in p["merge"]
    assert ("DIMS", "A-ANNO-DIMS") in p["merge"]
    assert p["already_standard"] == ["A-ANNO-DIMS"]
    assert p["non_standard"] == ["Junk"]
