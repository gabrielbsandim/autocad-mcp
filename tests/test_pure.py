import math
import os

import pytest

from autocad_mcp import files
from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import (
    bulge_polygon_area,
    bulge_polyline_length,
    flat_to_points,
    interior_point,
    offset_polyline,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
    polyline_length,
)
from autocad_mcp.layer_standard import DEFAULT_STANDARD, load_standard, plan, target_for
from autocad_mcp.rooms import looks_like_area, pick_name
from autocad_mcp.units import area_to_m2, fmt_num, meters_per_unit

SQUARE = [(0, 0), (4, 0), (4, 3), (0, 3)]
U_SHAPE = [(4, 4), (10, 4), (10, 7), (9, 7), (9, 5), (5, 5), (5, 7), (4, 7)]


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


def test_bulge_area_adds_outward_arcs_and_removes_inward_ones():
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    outward = bulge_polygon_area(square, [1, 0, 0, 0])
    assert outward == pytest.approx(4 + math.pi / 2)
    inward = bulge_polygon_area(square, [-1, 0, 0, 0])
    assert inward == pytest.approx(4 - math.pi / 2)
    clockwise = list(reversed(square))
    assert bulge_polygon_area(clockwise, [0, 0, 0, -1]) == pytest.approx(4 + math.pi / 2)


def test_bulge_length_uses_the_arc():
    assert bulge_polyline_length([(0, 0), (2, 0)], [1, 0], closed=False) == pytest.approx(math.pi)


def test_interior_point_stays_inside_u_shaped_rooms():
    centroid = polygon_centroid(U_SHAPE)
    assert not point_in_polygon(centroid, U_SHAPE)
    assert point_in_polygon(interior_point(U_SHAPE), U_SHAPE)
    assert interior_point(SQUARE) == pytest.approx(polygon_centroid(SQUARE))


def test_offset_closed_goes_outward_for_positive_distance():
    out = offset_polyline(SQUARE, 1, closed=True)
    assert polygon_area(out) == pytest.approx(6 * 5)
    inner = offset_polyline(list(reversed(SQUARE)), -0.5, closed=True)
    assert polygon_area(inner) == pytest.approx(3 * 2)


def test_offset_open_goes_left():
    assert offset_polyline([(0, 0), (4, 0)], 1, closed=False) == [pytest.approx((0, 1)), pytest.approx((4, 1))]
    corner = offset_polyline([(0, 0), (4, 0), (4, 3)], 1, closed=False)
    assert corner[1] == pytest.approx((3, 1))


def test_units_and_locale(monkeypatch):
    assert meters_per_unit(5)[0] == 0.01
    assert meters_per_unit(0)[0] is None
    assert meters_per_unit(0, "cm")[0] == 0.01
    with pytest.raises(ValueError):
        meters_per_unit(4, "in")
    assert area_to_m2(120000, 0.01) == pytest.approx(12)
    assert fmt_num(1234.5, loc="pt-BR") == "1.234,50"
    assert fmt_num(1234.5, loc="en-US") == "1,234.50"
    assert fmt_num(None) == "-"
    monkeypatch.setenv("AUTOCAD_MCP_LOCALE", "en-US")
    assert fmt_num(0.5) == "0.50"


def test_area_labels_and_room_numbers():
    assert looks_like_area("12,50 m²")
    assert looks_like_area("A = 12.5")
    assert looks_like_area("12,50")
    assert looks_like_area("1.234,50")
    assert not looks_like_area("101")
    assert not looks_like_area("SALA")
    assert pick_name(["101", "QUARTO", "12,00 m²"]) == ("QUARTO", "101")
    assert pick_name(["101"]) == ("101", "101")
    assert pick_name([]) == ("(unnamed)", "")


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


@pytest.mark.parametrize("bad", ["\\\\host\\share\\x.dwg", "//host/share/x.dwg", "relative/x.dwg",
                                 "http://example.org/x.dwg", ""])
def test_safe_path_refuses_network_relative_and_urls(bad):
    with pytest.raises(AcadError):
        files.safe_path(bad)


def test_safe_path_respects_allowed_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_ALLOWED_DIRS", str(tmp_path / "ok"))
    assert files.safe_path(str(tmp_path / "ok" / "a.dxf")).endswith("a.dxf")
    with pytest.raises(AcadError, match="outside"):
        files.safe_path(str(tmp_path / "other" / "a.dxf"))
    with pytest.raises(AcadError, match="extensions"):
        files.safe_path(str(tmp_path / "ok" / "a.exe"), extensions={".dxf"})


def test_csv_is_excel_safe_and_follows_the_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCAD_MCP_LOCALE", "pt-BR")
    path = tmp_path / "x.csv"
    files.write_csv(str(path), ["Room", "Area"], [["=1+1", 12.5], ["-1+1", 3], ["SALA", -2.25]])
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "Room;Area"
    assert lines[1] == "'=1+1;12,50"
    assert lines[2] == "'-1+1;3"
    assert lines[3] == "SALA;-2,25"


def test_output_path_refuses_overwrite(tmp_path):
    existing = tmp_path / "a.csv"
    existing.write_text("x")
    with pytest.raises(AcadError, match="already exists"):
        files.output_path(str(existing), extensions={".csv"}, overwrite=False)
    assert files.output_path(str(existing), extensions={".csv"}, overwrite=True) == os.path.abspath(existing)
