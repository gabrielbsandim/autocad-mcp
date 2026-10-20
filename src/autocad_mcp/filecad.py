"""Drawings edited as files through ezdxf, with no CAD running: Mac, AutoCAD LT, Linux and the tests.

The classes mirror the slice of the AutoCAD COM object model the tools use, so every tool runs
unchanged on both backends. Changes stay in memory until save_drawing writes the file.
"""
from __future__ import annotations

import io
import math
import os
from collections.abc import Iterable, Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf import path as ezpath
from ezdxf.addons import odafc
from ezdxf.math import Matrix44

from autocad_mcp.errors import AcadError
from autocad_mcp.geometry import (
    bulge_polygon_area,
    bulge_polyline_length,
    offset_polyline,
    point_in_polygon,
    polygon_area,
)

UNDO_DEPTH = 20
FLATTEN = 0.001
PROTECTED_LAYERS = {"0", "DEFPOINTS"}

ODA_HELP = ("DWG files need the free ODA File Converter, installed by the user from "
            "https://www.opendesign.com/guestfiles/oda_file_converter (free for non-commercial use). "
            "Without it, save the drawing as DXF in the CAD and open the .dxf.")

OBJECT_NAMES = {
    "LINE": "AcDbLine", "LWPOLYLINE": "AcDbPolyline", "CIRCLE": "AcDbCircle", "ARC": "AcDbArc",
    "TEXT": "AcDbText", "MTEXT": "AcDbMText", "INSERT": "AcDbBlockReference", "HATCH": "AcDbHatch",
    "SPLINE": "AcDbSpline", "ELLIPSE": "AcDbEllipse", "POINT": "AcDbPoint", "ATTDEF": "AcDbAttributeDefinition",
    "ATTRIB": "AcDbAttribute", "ACAD_TABLE": "AcDbTable", "LEADER": "AcDbLeader", "MLEADER": "AcDbMLeader",
    "XLINE": "AcDbXline", "RAY": "AcDbRay", "VIEWPORT": "AcDbViewport", "IMAGE": "AcDbRasterImage",
    "WIPEOUT": "AcDbWipeout", "SOLID": "AcDbTrace", "3DFACE": "AcDbFace",
}
DIMENSION_NAMES = {
    0: "AcDbRotatedDimension", 1: "AcDbAlignedDimension", 2: "AcDb2LineAngularDimension",
    3: "AcDbDiametricDimension", 4: "AcDbRadialDimension", 5: "AcDb3PointAngularDimension",
    6: "AcDbOrdinateDimension",
}
TEXT_ALIGNMENT = {
    0: (0, 0), 1: (1, 0), 2: (2, 0), 3: (3, 0), 4: (4, 0), 5: (5, 0), 6: (0, 3), 7: (1, 3), 8: (2, 3),
    9: (0, 2), 10: (1, 2), 11: (2, 2), 12: (0, 1), 13: (1, 1), 14: (2, 1),
}


class FileError(Exception):
    """A request the file backend cannot honour, phrased for the user."""


def _xyz(p: Sequence[float]) -> tuple[float, float, float]:
    vals = [float(v) for v in p]
    return (vals[0], vals[1], vals[2] if len(vals) > 2 else 0.0)


def _xy(p: Sequence[float]) -> tuple[float, float]:
    return (float(p[0]), float(p[1]))


def _flatten_points(entity: Any) -> list[tuple[float, float]]:
    return [(v.x, v.y) for v in ezpath.make_path(entity).flattening(FLATTEN)]


class FileBackend:
    name = "file"
    label = "File mode"

    def __init__(self) -> None:
        self._app = FileApp()

    def init_thread(self) -> None:
        return None

    def app(self) -> FileApp:
        return self._app

    def point(self, x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        return (float(x), float(y), float(z))

    def flat(self, values: Iterable[float]) -> tuple[float, ...]:
        return tuple(float(v) for v in values)

    def objects(self, objs: Sequence[Any]) -> list[Any]:
        return list(objs)

    def translate(self, exc: BaseException) -> AcadError | None:
        if isinstance(exc, FileError):
            return AcadError(str(exc))
        if isinstance(exc, ezdxf.DXFError):
            return AcadError(f"DXF error: {exc}")
        if isinstance(exc, OSError):
            return AcadError(f"File error: {exc}")
        return None

    def is_busy(self, exc: BaseException) -> bool:
        return False

    def is_fatal(self, exc: BaseException) -> bool:
        return False

    def quiescent(self) -> bool:
        return True

    def undo(self, document: FileDocument, steps: int) -> None:
        document.restore(steps)


class FileApp:
    Name = "File mode (ezdxf)"

    def __init__(self) -> None:
        self.docs: list[FileDocument] = []
        self.active: FileDocument | None = None
        self._counter = 0

    @property
    def Version(self) -> str:
        return ezdxf.__version__

    @property
    def Documents(self) -> FileDocuments:
        return FileDocuments(self)

    @property
    def ActiveDocument(self) -> FileDocument:
        if self.active is None:
            raise FileError("No drawing is open.")
        return self.active

    def GetAcadState(self) -> SimpleNamespace:
        return SimpleNamespace(IsQuiescent=True)

    def next_name(self) -> str:
        self._counter += 1
        return f"Drawing{self._counter}.dxf"

    def adopt(self, document: FileDocument) -> FileDocument:
        self.docs.append(document)
        self.active = document
        return document


class FileDocuments:
    def __init__(self, app: FileApp) -> None:
        self._app = app

    @property
    def Count(self) -> int:
        return len(self._app.docs)

    def __iter__(self) -> Iterator[FileDocument]:
        return iter(list(self._app.docs))

    def Item(self, index: int) -> FileDocument:
        return self._app.docs[index]

    def Add(self, template: str = "") -> FileDocument:
        drawing = load(template) if template else ezdxf.new("R2018", setup=True)
        return self._app.adopt(FileDocument(self._app, drawing, name=self._app.next_name()))

    def Open(self, path: str, read_only: bool = True) -> FileDocument:
        for existing in self._app.docs:
            if existing.FullName and os.path.samefile(existing.FullName, path):
                self._app.active = existing
                return existing
        return self._app.adopt(FileDocument(self._app, load(path), path=path, read_only=read_only))


def load(path: str) -> Any:
    if path.lower().endswith(".dwg"):
        if not odafc.is_installed():
            raise FileError(ODA_HELP)
        return odafc.readfile(path)
    return ezdxf.readfile(path)


class FileDocument:
    def __init__(self, app: FileApp, drawing: Any, path: str = "", read_only: bool = False, name: str = "") -> None:
        self.app = app
        self.dxf = drawing
        self.FullName = path
        self.ReadOnly = bool(read_only)
        self._name = name or os.path.basename(path)
        self._undo: list[str] = []
        self._pending: str | None = None
        self._depth = 0
        self.modified = False
        self.tables: list[FileTable] = []
        self.last_audit: dict[str, int] | None = None
        self.ActiveLayout: Any = None

    @property
    def Name(self) -> str:
        return self._name

    @property
    def ModelSpace(self) -> FileSpace:
        return FileSpace(self, self.dxf.modelspace())

    @property
    def PaperSpace(self) -> FileSpace:
        return FileSpace(self, self.dxf.paperspace())

    @property
    def Layouts(self) -> list[FileLayout]:
        names = self.dxf.layouts.names_in_taborder()
        return [FileLayout(self, self.dxf.layouts.get(n), i) for i, n in enumerate(names)]

    @property
    def Layers(self) -> FileLayers:
        return FileLayers(self)

    @property
    def Blocks(self) -> FileBlocks:
        return FileBlocks(self)

    @property
    def Linetypes(self) -> FileLinetypes:
        return FileLinetypes(self)

    @property
    def TextStyles(self) -> SimpleNamespace:
        return SimpleNamespace(Count=len(self.dxf.styles))

    @property
    def DimStyles(self) -> SimpleNamespace:
        return SimpleNamespace(Count=len(self.dxf.dimstyles))

    @property
    def PickfirstSelectionSet(self) -> SimpleNamespace:
        return SimpleNamespace(Count=0, Item=lambda i: None)

    def wrap(self, entity: Any) -> FileEntity:
        return FileEntity(self, entity)

    def GetVariable(self, name: str) -> Any:
        key = name.upper()
        if key == "DBMOD":
            return 1 if self.modified else 0
        if key == "BACKGROUNDPLOT":
            return 0
        if key in ("EXTMIN", "EXTMAX"):
            ext = ezbbox.extents(self.dxf.modelspace(), fast=True)
            if not ext.has_data:
                return (0.0, 0.0, 0.0)
            return tuple(ext.extmin if key == "EXTMIN" else ext.extmax)
        header_key = f"${key}"
        if header_key not in self.dxf.header:
            if key in ("INSUNITS", "CLAYER"):
                return 0 if key == "INSUNITS" else "0"
            raise FileError(f"Unknown system variable {key}.")
        value = self.dxf.header[header_key]
        return tuple(value) if hasattr(value, "__iter__") and not isinstance(value, str) else value

    def SetVariable(self, name: str, value: Any) -> None:
        try:
            self.dxf.header[f"${name.upper()}"] = value
        except Exception as exc:  # noqa: BLE001
            raise FileError(f"Variable {name.upper()} cannot be set in a DXF file: {exc}") from exc
        self.modified = True

    def HandleToObject(self, handle: str) -> FileEntity:
        entity = self.dxf.entitydb.get(str(handle).upper())
        if entity is None or not entity.is_alive or not hasattr(entity, "get_layout"):
            raise FileError(f"No object with handle {handle!r}.")
        return self.wrap(entity)

    def StartUndoMark(self) -> None:
        if self._depth == 0:
            self._pending = self._snapshot()
        self._depth += 1

    def EndUndoMark(self) -> None:
        self._depth = max(0, self._depth - 1)
        if self._depth or self._pending is None:
            return
        for table in self.tables:
            table.render()
        self.tables.clear()
        self._undo.append(self._pending)
        del self._undo[:-UNDO_DEPTH]
        self._pending = None
        self.modified = True

    def _snapshot(self) -> str:
        stream = io.StringIO()
        self.dxf.write(stream)
        return stream.getvalue()

    def restore(self, steps: int) -> None:
        if steps < 1 or steps > len(self._undo):
            raise FileError(f"Only {len(self._undo)} change(s) can be undone in this drawing.")
        snapshot = self._undo[-steps]
        del self._undo[-steps:]
        self.dxf = ezdxf.read(io.StringIO(snapshot))
        self.modified = True

    def Save(self) -> None:
        if self.ReadOnly:
            raise FileError("This drawing was opened read-only; save a copy with another file name.")
        if not self.FullName:
            raise FileError("The drawing was never saved; give a file path.")
        self._write(self.FullName)

    def SaveAs(self, path: str, save_type: int | None = None) -> None:
        self._write(path)
        self.FullName = path
        self._name = os.path.basename(path)
        self.ReadOnly = False

    def _write(self, path: str) -> None:
        if path.lower().endswith(".dwg"):
            if not odafc.is_installed():
                raise FileError(ODA_HELP)
            odafc.export_dwg(self.dxf, path, replace=True)
        else:
            self.dxf.saveas(path)
        self.modified = False

    def Activate(self) -> None:
        self.app.active = self

    def Close(self, save: bool = False) -> None:
        self.app.docs.remove(self)
        self.app.active = self.app.docs[-1] if self.app.docs else None

    @property
    def ActiveLayer(self) -> FileLayer:
        return self.Layers.Item(self.GetVariable("CLAYER"))

    @ActiveLayer.setter
    def ActiveLayer(self, layer: FileLayer) -> None:
        self.dxf.header["$CLAYER"] = layer.Name

    def Regen(self, which: int = 0) -> None:
        return None

    def SendCommand(self, command: str) -> None:
        raise FileError("Typing commands is only possible with a CAD running.")

    def AuditInfo(self, fix: bool = True) -> None:
        auditor = self.dxf.audit()
        self.last_audit = {"errors": len(auditor.errors), "fixes": len(auditor.fixes)}

    def entities_on_layer(self, name: str) -> Iterator[Any]:
        up = name.upper()
        for entity in self.dxf.entitydb.values():
            if entity.is_alive and hasattr(entity, "get_layout") and entity.dxf.get("layer", "0").upper() == up:
                yield entity

    def PurgeAll(self) -> None:
        current = str(self.GetVariable("CLAYER")).upper()
        used_layers = {e.dxf.get("layer", "0").upper() for e in self.dxf.entitydb.values()
                       if e.is_alive and hasattr(e, "get_layout")}
        for layer in list(self.dxf.layers):
            name = layer.dxf.name
            if name.upper() in PROTECTED_LAYERS | {current} | used_layers:
                continue
            self.dxf.layers.remove(name)
        inserted = {e.dxf.name.upper() for e in self.dxf.entitydb.values() if e.is_alive and e.dxftype() == "INSERT"}
        for block in list(self.dxf.blocks):
            record = block.block_record
            if record.is_any_layout or record.is_xref or block.name.startswith("*"):
                continue
            if block.name.upper() not in inserted:
                self.dxf.blocks.delete_block(block.name, safe=False)

    @property
    def Plot(self) -> Any:
        raise FileError("Plotting to PDF from a file needs a CAD running; use the preview export instead.")


class FileLayout:
    def __init__(self, document: FileDocument, layout: Any, order: int) -> None:
        self._doc = document
        self._layout = layout
        self.TabOrder = order

    @property
    def Name(self) -> str:
        return self._layout.name

    @property
    def ConfigName(self) -> str:
        return self._layout.dxf_layout.dxf.get("plot_configuration_file", "")

    @property
    def CanonicalMediaName(self) -> str:
        return self._layout.dxf_layout.dxf.get("paper_size", "")

    @property
    def StyleSheet(self) -> str:
        return self._layout.dxf_layout.dxf.get("current_style_sheet", "")

    @property
    def Block(self) -> FileSpace:
        return FileSpace(self._doc, self._layout)


class FileSpace:
    def __init__(self, document: FileDocument, layout: Any) -> None:
        self._doc = document
        self.layout = layout

    def __iter__(self) -> Iterator[FileEntity]:
        return (self._doc.wrap(e) for e in list(self.layout))

    @property
    def Count(self) -> int:
        return len(self.layout)

    def _add(self, entity: Any) -> FileEntity:
        entity.dxf.layer = str(self._doc.GetVariable("CLAYER"))
        return self._doc.wrap(entity)

    def AddLine(self, p1: Sequence[float], p2: Sequence[float]) -> FileEntity:
        return self._add(self.layout.add_line(_xyz(p1), _xyz(p2)))

    def AddLightWeightPolyline(self, flat: Sequence[float]) -> FileEntity:
        pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        return self._add(self.layout.add_lwpolyline(pts))

    def AddCircle(self, center: Sequence[float], radius: float) -> FileEntity:
        return self._add(self.layout.add_circle(_xyz(center), radius))

    def AddArc(self, center: Sequence[float], radius: float, start: float, end: float) -> FileEntity:
        return self._add(self.layout.add_arc(_xyz(center), radius, math.degrees(start), math.degrees(end)))

    def AddText(self, text: str, point: Sequence[float], height: float) -> FileEntity:
        return self._add(self.layout.add_text(text, dxfattribs={"height": height, "insert": _xyz(point)}))

    def AddMText(self, point: Sequence[float], width: float, text: str) -> FileEntity:
        return self._add(self.layout.add_mtext(text, dxfattribs={"insert": _xyz(point), "width": width}))

    def AddPoint(self, point: Sequence[float]) -> FileEntity:
        return self._add(self.layout.add_point(_xyz(point)))

    def _dimstyle(self) -> str:
        return "EZDXF" if "EZDXF" in self._doc.dxf.dimstyles else "Standard"

    def AddDimAligned(self, p1: Sequence[float], p2: Sequence[float], location: Sequence[float]) -> FileEntity:
        (x1, y1), (x2, y2), (lx, ly) = _xy(p1), _xy(p2), _xy(location)
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        distance = ((x2 - x1) * (ly - y1) - (y2 - y1) * (lx - x1)) / length
        dim = self.layout.add_aligned_dim(p1=_xyz(p1), p2=_xyz(p2), distance=distance, dimstyle=self._dimstyle())
        dim.render()
        return self._add(dim.dimension)

    def AddDimRotated(self, p1: Sequence[float], p2: Sequence[float], location: Sequence[float],
                      angle: float) -> FileEntity:
        dim = self.layout.add_linear_dim(base=_xyz(location), p1=_xyz(p1), p2=_xyz(p2), angle=math.degrees(angle),
                                         dimstyle=self._dimstyle())
        dim.render()
        return self._add(dim.dimension)

    def AddHatch(self, pattern_type: int, pattern: str, associative: bool) -> FileEntity:
        hatch = self.layout.add_hatch()
        wrapped = self._add(hatch)
        wrapped.set_pattern(pattern)
        return wrapped

    def InsertBlock(self, point: Sequence[float], name: str, xs: float, ys: float, zs: float,
                    rotation: float) -> FileEntity:
        block_name = _import_block(self._doc, name) if name.lower().endswith((".dxf", ".dwg")) else name
        if block_name not in self._doc.dxf.blocks:
            raise FileError(f"Block {name!r} is not defined in this drawing.")
        ref = self.layout.add_blockref(block_name, _xyz(point), dxfattribs={
            "xscale": xs, "yscale": ys, "zscale": zs, "rotation": math.degrees(rotation)})
        ref.add_auto_attribs({})
        return self._add(ref)

    def AddTable(self, point: Sequence[float], rows: int, cols: int, row_height: float,
                 col_width: float) -> FileTable:
        table = FileTable(self._doc, self.layout, _xyz(point), rows, cols, row_height, col_width)
        self._doc.tables.append(table)
        return table


def _import_block(document: FileDocument, path: str) -> str:
    from ezdxf.addons import Importer

    if not os.path.isfile(path):
        raise FileError(f"File not found: {path}")
    source = load(path)
    name = os.path.splitext(os.path.basename(path))[0]
    if name in document.dxf.blocks:
        return name
    block = document.dxf.blocks.new(name)
    importer = Importer(source, document.dxf)
    importer.import_entities(source.modelspace(), block)
    importer.finalize()
    return name


class FileEntity:
    def __init__(self, document: FileDocument, entity: Any) -> None:
        self._doc = document
        self.e = entity

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FileEntity) and other.e is self.e

    def __hash__(self) -> int:
        return id(self.e)

    @property
    def _type(self) -> str:
        return self.e.dxftype()

    def _unsupported(self, member: str) -> FileError:
        return FileError(f"{self.ObjectName} has no {member}.")

    @property
    def ObjectName(self) -> str:
        t = self._type
        if t == "DIMENSION":
            return DIMENSION_NAMES.get(self.e.dxf.dimtype & 0x0F, "AcDbDimension")
        if t == "POLYLINE":
            return "AcDb3dPolyline" if self.e.is_3d_polyline else "AcDb2dPolyline"
        return OBJECT_NAMES.get(t, "AcDb" + t.title())

    @property
    def Handle(self) -> str:
        return self.e.dxf.handle

    @property
    def Layer(self) -> str:
        return self.e.dxf.get("layer", "0")

    @Layer.setter
    def Layer(self, name: str) -> None:
        layer = self._doc.Layers.Item(name)
        self.e.dxf.layer = layer.Name

    @property
    def Color(self) -> int:
        return self.e.dxf.get("color", 256)

    @Color.setter
    def Color(self, value: int) -> None:
        self.e.dxf.color = int(value)

    @property
    def Linetype(self) -> str:
        return self.e.dxf.get("linetype", "ByLayer")

    @Linetype.setter
    def Linetype(self, name: str) -> None:
        if name.lower() not in ("bylayer", "byblock") and name not in self._doc.dxf.linetypes:
            raise FileError(f"Linetype {name!r} is not loaded.")
        self.e.dxf.linetype = name

    @property
    def Lineweight(self) -> int:
        return self.e.dxf.get("lineweight", -1)

    @Lineweight.setter
    def Lineweight(self, value: int) -> None:
        self.e.dxf.lineweight = int(value)

    def GetBoundingBox(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        ext = ezbbox.extents([self.e], fast=False)
        if not ext.has_data:
            raise self._unsupported("extents")
        return tuple(ext.extmin), tuple(ext.extmax)

    def _layout(self) -> Any:
        layout = self.e.get_layout()
        if layout is None:
            raise FileError("This object is not in a drawing space.")
        return layout

    def _transform(self, matrix: Matrix44) -> None:
        self.e.transform(matrix)

    def Move(self, p1: Sequence[float], p2: Sequence[float]) -> None:
        a, b = _xyz(p1), _xyz(p2)
        self._transform(Matrix44.translate(b[0] - a[0], b[1] - a[1], b[2] - a[2]))

    def Copy(self) -> FileEntity:
        clone = self.e.copy()
        self._layout().add_entity(clone)
        return self._doc.wrap(clone)

    def Rotate(self, base: Sequence[float], angle: float) -> None:
        bx, by, _ = _xyz(base)
        self._transform(Matrix44.chain(Matrix44.translate(-bx, -by, 0), Matrix44.z_rotate(angle),
                                       Matrix44.translate(bx, by, 0)))

    def ScaleEntity(self, base: Sequence[float], factor: float) -> None:
        bx, by, _ = _xyz(base)
        self._transform(Matrix44.chain(Matrix44.translate(-bx, -by, 0), Matrix44.scale(factor),
                                       Matrix44.translate(bx, by, 0)))

    def Mirror(self, p1: Sequence[float], p2: Sequence[float]) -> FileEntity:
        (x1, y1), (x2, y2) = _xy(p1), _xy(p2)
        angle = math.atan2(y2 - y1, x2 - x1)
        clone = self.Copy()
        clone._transform(Matrix44.chain(Matrix44.translate(-x1, -y1, 0), Matrix44.z_rotate(-angle),
                                        Matrix44.scale(1, -1, 1), Matrix44.z_rotate(angle),
                                        Matrix44.translate(x1, y1, 0)))
        return clone

    def Offset(self, distance: float) -> list[FileEntity]:
        t = self._type
        layout = self._layout()
        attribs = {"layer": self.Layer}
        if t in ("CIRCLE", "ARC"):
            radius = self.e.dxf.radius + distance
            if radius <= 0:
                raise FileError("The offset distance is larger than the radius.")
            clone = self.Copy()
            clone.e.dxf.radius = radius
            return [clone]
        if t == "LINE":
            pts = offset_polyline([_xy(self.e.dxf.start), _xy(self.e.dxf.end)], distance, False)
            return [self._doc.wrap(layout.add_line(pts[0], pts[1], dxfattribs=attribs))]
        if t == "LWPOLYLINE":
            if any(b for *_, b in self.e.get_points("xyb")):
                raise FileError("Offsetting polylines with arcs needs a CAD running.")
            pts = offset_polyline([(x, y) for x, y in self.e.get_points("xy")], distance, self.e.closed)
            new = layout.add_lwpolyline(pts, dxfattribs=attribs)
            new.closed = self.e.closed
            return [self._doc.wrap(new)]
        raise self._unsupported("offset in file mode")

    def Delete(self) -> None:
        self._layout().delete_entity(self.e)

    # ---------------------------------------------------------------- geometry

    @property
    def StartPoint(self) -> tuple[float, ...]:
        if self._type == "ARC":
            return tuple(self.e.start_point)
        return tuple(self.e.dxf.start)

    @property
    def EndPoint(self) -> tuple[float, ...]:
        if self._type == "ARC":
            return tuple(self.e.end_point)
        return tuple(self.e.dxf.end)

    @property
    def Center(self) -> tuple[float, ...]:
        return tuple(self.e.dxf.center)

    @property
    def Radius(self) -> float:
        return float(self.e.dxf.radius)

    @property
    def Circumference(self) -> float:
        return 2 * math.pi * self.Radius

    @property
    def ArcLength(self) -> float:
        span = (self.e.dxf.end_angle - self.e.dxf.start_angle) % 360 or 360
        return self.Radius * math.radians(span)

    def _bulged(self) -> tuple[list[tuple[float, float]], list[float]]:
        if self._type == "LWPOLYLINE":
            pts = list(self.e.get_points("xyb"))
            return [(float(x), float(y)) for x, y, _ in pts], [float(b) for *_, b in pts]
        verts = list(self.e.vertices)
        return [_xy(v.dxf.location) for v in verts], [float(v.dxf.get("bulge", 0)) for v in verts]

    @property
    def Coordinates(self) -> tuple[float, ...]:
        if self._type == "LWPOLYLINE":
            return tuple(c for x, y in self.e.get_points("xy") for c in (float(x), float(y)))
        return tuple(c for v in self.e.vertices for c in _xyz(v.dxf.location))

    @property
    def Closed(self) -> bool:
        if self._type == "LWPOLYLINE":
            return bool(self.e.closed)
        return bool(self.e.is_closed)

    @Closed.setter
    def Closed(self, value: bool) -> None:
        if self._type == "LWPOLYLINE":
            self.e.closed = bool(value)
            return
        self.e.close(bool(value))

    @property
    def Length(self) -> float:
        t = self._type
        if t == "LINE":
            return math.dist(_xyz(self.e.dxf.start), _xyz(self.e.dxf.end))
        if t in ("LWPOLYLINE", "POLYLINE"):
            pts, bulges = self._bulged()
            return bulge_polyline_length(pts, bulges, self.Closed)
        if t in ("SPLINE", "ELLIPSE"):
            pts = _flatten_points(self.e)
            return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        raise self._unsupported("Length")

    @property
    def Area(self) -> float:
        t = self._type
        if t == "CIRCLE":
            return math.pi * self.Radius ** 2
        if t in ("LWPOLYLINE", "POLYLINE"):
            pts, bulges = self._bulged()
            return bulge_polygon_area(pts, bulges)
        if t in ("SPLINE", "ELLIPSE"):
            return polygon_area(_flatten_points(self.e))
        if t == "HATCH":
            return _hatch_area(self.e)
        raise self._unsupported("Area")

    # ---------------------------------------------------------------- text

    @property
    def TextString(self) -> str:
        if self._type == "MTEXT":
            return self.e.text
        return self.e.dxf.get("text", "")

    @TextString.setter
    def TextString(self, value: str) -> None:
        if self._type == "MTEXT":
            self.e.text = value
            return
        self.e.dxf.text = value

    @property
    def InsertionPoint(self) -> tuple[float, ...]:
        return tuple(self.e.dxf.insert)

    @property
    def Height(self) -> float:
        if self._type == "MTEXT":
            return float(self.e.dxf.get("char_height", 1.0))
        return float(self.e.dxf.get("height", 1.0))

    @Height.setter
    def Height(self, value: float) -> None:
        if self._type == "MTEXT":
            self.e.dxf.char_height = float(value)
            return
        self.e.dxf.height = float(value)

    @property
    def Rotation(self) -> float:
        return math.radians(self.e.dxf.get("rotation", 0.0))

    @Rotation.setter
    def Rotation(self, value: float) -> None:
        self.e.dxf.rotation = math.degrees(value)

    @property
    def Alignment(self) -> int:
        pair = (self.e.dxf.get("halign", 0), self.e.dxf.get("valign", 0))
        return next((k for k, v in TEXT_ALIGNMENT.items() if v == pair), 0)

    @Alignment.setter
    def Alignment(self, value: int) -> None:
        self.e.dxf.halign, self.e.dxf.valign = TEXT_ALIGNMENT[int(value)]

    @property
    def TextAlignmentPoint(self) -> tuple[float, ...]:
        return tuple(self.e.dxf.get("align_point", self.e.dxf.insert))

    @TextAlignmentPoint.setter
    def TextAlignmentPoint(self, point: Sequence[float]) -> None:
        self.e.dxf.align_point = _xyz(point)
        self.e.dxf.insert = _xyz(point)

    # ---------------------------------------------------------------- blocks

    @property
    def Name(self) -> str:
        return self.e.dxf.name

    @property
    def EffectiveName(self) -> str:
        return self.e.dxf.name

    @property
    def TagString(self) -> str:
        return self.e.dxf.tag

    @property
    def HasAttributes(self) -> bool:
        return bool(self.e.attribs)

    def GetAttributes(self) -> list[FileAttribute]:
        return [FileAttribute(a) for a in self.e.attribs]

    @property
    def XScaleFactor(self) -> float:
        return float(self.e.dxf.get("xscale", 1.0))

    # ---------------------------------------------------------------- hatches and dimensions

    @property
    def PatternName(self) -> str:
        return self.e.dxf.get("pattern_name", "")

    def set_pattern(self, pattern: str, scale: float = 1.0, angle: float = 0.0) -> None:
        self._pattern = (pattern, scale, angle)
        if pattern.upper() == "SOLID":
            self.e.set_solid_fill()
            return
        self.e.set_pattern_fill(pattern.upper(), scale=scale, angle=angle)

    @property
    def PatternScale(self) -> float:
        return float(self.e.dxf.get("pattern_scale", 1.0))

    @PatternScale.setter
    def PatternScale(self, value: float) -> None:
        self.set_pattern(self.PatternName, float(value), self.e.dxf.get("pattern_angle", 0.0))

    @property
    def PatternAngle(self) -> float:
        return math.radians(self.e.dxf.get("pattern_angle", 0.0))

    @PatternAngle.setter
    def PatternAngle(self, value: float) -> None:
        self.set_pattern(self.PatternName, self.PatternScale, math.degrees(value))

    def AppendOuterLoop(self, loop: Sequence[FileEntity]) -> None:
        for item in loop:
            if item.e.dxftype() == "LWPOLYLINE":
                self.e.paths.add_polyline_path(list(item.e.get_points("xyb")), is_closed=True)
                continue
            self.e.paths.add_polyline_path(_flatten_points(item.e), is_closed=True)

    def Evaluate(self) -> None:
        return None

    @property
    def Measurement(self) -> float:
        value = self.e.get_measurement()
        return float(value) if isinstance(value, (int, float)) else float(abs(value))

    @property
    def TextOverride(self) -> str:
        text = self.e.dxf.get("text", "")
        return "" if text in ("", "<>") else text


class FileAttribute:
    def __init__(self, attrib: Any) -> None:
        self.a = attrib

    @property
    def TagString(self) -> str:
        return self.a.dxf.tag

    @property
    def TextString(self) -> str:
        return self.a.dxf.text

    @TextString.setter
    def TextString(self, value: str) -> None:
        self.a.dxf.text = value


def _hatch_area(hatch: Any) -> float:
    polygons = []
    for boundary in ezpath.from_hatch(hatch):
        pts = [(v.x, v.y) for v in boundary.flattening(FLATTEN)]
        if len(pts) > 2:
            polygons.append(pts)
    total = 0.0
    for i, poly in enumerate(polygons):
        depth = sum(1 for j, other in enumerate(polygons) if j != i and point_in_polygon(poly[0], other))
        total += polygon_area(poly) * (-1 if depth % 2 else 1)
    return abs(total)


class FileTable(FileEntity):
    """AutoCAD tables do not exist in DXF written by ezdxf, so the table is drawn as a block of
    lines and text when the undo group closes, after every cell has been set."""

    _count = 0

    def __init__(self, document: FileDocument, layout: Any, point: tuple[float, float, float], rows: int,
                 cols: int, row_height: float, col_width: float) -> None:
        FileTable._count += 1
        name = f"MCP_TABLE_{FileTable._count}"
        while name in document.dxf.blocks:
            FileTable._count += 1
            name = f"MCP_TABLE_{FileTable._count}"
        self.block = document.dxf.blocks.new(name)
        super().__init__(document, layout.add_blockref(name, point))
        self.e.dxf.layer = str(document.GetVariable("CLAYER"))
        self.rows, self.cols = rows, cols
        self.row_height = row_height
        self.widths = [col_width] * cols
        self.heights: dict[tuple[int, int], float] = {}
        self.cells: dict[tuple[int, int], str] = {}

    @property
    def ObjectName(self) -> str:
        return "AcDbTable"

    def SetColumnWidth(self, col: int, width: float) -> None:
        self.widths[col] = float(width)

    def SetCellTextHeight(self, row: int, col: int, height: float) -> None:
        self.heights[(row, col)] = float(height)

    def SetText(self, row: int, col: int, text: str) -> None:
        self.cells[(row, col)] = str(text)

    def GetText(self, row: int, col: int) -> str:
        return self.cells.get((row, col), "")

    def render(self) -> None:
        for entity in list(self.block):
            self.block.delete_entity(entity)
        total_w = sum(self.widths)
        total_h = self.rows * self.row_height
        for r in range(self.rows + 1):
            self.block.add_line((0, -r * self.row_height), (total_w, -r * self.row_height))
        x = 0.0
        for width in [0.0, *self.widths]:
            x += width
            self.block.add_line((x, 0), (x, -total_h))
        for (r, c), text in self.cells.items():
            height = self.heights.get((r, c), self.row_height / 2.2)
            left = sum(self.widths[:c])
            cx = left + self.widths[c] / 2 if r == 0 else left + height * 0.5
            cy = -(r + 0.5) * self.row_height
            entity = self.block.add_text(text, dxfattribs={"height": height})
            entity.set_placement((cx, cy), align=_middle(r == 0))


def _middle(centered: bool) -> Any:
    from ezdxf.enums import TextEntityAlignment

    return TextEntityAlignment.MIDDLE_CENTER if centered else TextEntityAlignment.MIDDLE_LEFT


class FileLayers:
    def __init__(self, document: FileDocument) -> None:
        self._doc = document

    @property
    def Count(self) -> int:
        return len(self._doc.dxf.layers)

    def __iter__(self) -> Iterator[FileLayer]:
        return (FileLayer(self._doc, layer) for layer in list(self._doc.dxf.layers))

    def Item(self, name: str) -> FileLayer:
        try:
            return FileLayer(self._doc, self._doc.dxf.layers.get(str(name)))
        except ezdxf.DXFTableEntryError as exc:
            raise FileError(f"Layer {name!r} does not exist.") from exc

    def Add(self, name: str) -> FileLayer:
        if name in self._doc.dxf.layers:
            return self.Item(name)
        return FileLayer(self._doc, self._doc.dxf.layers.add(name, color=7))


class FileLayer:
    def __init__(self, document: FileDocument, layer: Any) -> None:
        self._doc = document
        self.layer = layer

    @property
    def Name(self) -> str:
        return self.layer.dxf.name

    @Name.setter
    def Name(self, new: str) -> None:
        old = self.Name
        if old.upper() in PROTECTED_LAYERS:
            raise FileError(f"Layer {old} cannot be renamed.")
        if new.upper() != old.upper() and new in self._doc.dxf.layers:
            raise FileError(f"Layer {new} already exists.")
        moved = list(self._doc.entities_on_layer(old))
        was_current = str(self._doc.GetVariable("CLAYER")).upper() == old.upper()
        self.layer.rename(new)
        for entity in moved:
            entity.dxf.layer = new
        if was_current:
            self._doc.dxf.header["$CLAYER"] = new

    @property
    def Color(self) -> int:
        return self.layer.color

    @Color.setter
    def Color(self, value: int) -> None:
        self.layer.color = int(value)

    @property
    def Linetype(self) -> str:
        return self.layer.dxf.get("linetype", "Continuous")

    @Linetype.setter
    def Linetype(self, name: str) -> None:
        if name not in self._doc.dxf.linetypes:
            raise FileError(f"Linetype {name!r} is not loaded.")
        self.layer.dxf.linetype = name

    @property
    def Lineweight(self) -> int:
        return self.layer.dxf.get("lineweight", -3)

    @Lineweight.setter
    def Lineweight(self, value: int) -> None:
        self.layer.dxf.lineweight = int(value)

    @property
    def LayerOn(self) -> bool:
        return self.layer.is_on()

    @LayerOn.setter
    def LayerOn(self, value: bool) -> None:
        self.layer.on() if value else self.layer.off()

    @property
    def Freeze(self) -> bool:
        return self.layer.is_frozen()

    @Freeze.setter
    def Freeze(self, value: bool) -> None:
        self.layer.freeze() if value else self.layer.thaw()

    @property
    def Lock(self) -> bool:
        return self.layer.is_locked()

    @Lock.setter
    def Lock(self, value: bool) -> None:
        self.layer.lock() if value else self.layer.unlock()

    @property
    def Plottable(self) -> bool:
        return bool(self.layer.dxf.get("plot", 1))

    @Plottable.setter
    def Plottable(self, value: bool) -> None:
        self.layer.dxf.plot = 1 if value else 0

    @property
    def Description(self) -> str:
        return self.layer.description

    @Description.setter
    def Description(self, value: str) -> None:
        self.layer.description = value

    def Delete(self) -> None:
        name = self.Name
        if name.upper() in PROTECTED_LAYERS or name.upper() == str(self._doc.GetVariable("CLAYER")).upper():
            raise FileError(f"Layer {name} cannot be deleted.")
        if next(self._doc.entities_on_layer(name), None) is not None:
            raise FileError(f"Layer {name} is still referenced.")
        self._doc.dxf.layers.remove(name)


class FileBlocks:
    def __init__(self, document: FileDocument) -> None:
        self._doc = document

    @property
    def Count(self) -> int:
        return len(self._doc.dxf.blocks)

    def __iter__(self) -> Iterator[FileBlock]:
        return (FileBlock(self._doc, b) for b in list(self._doc.dxf.blocks))

    def Item(self, name: str) -> FileBlock:
        block = self._doc.dxf.blocks.get(name)
        if block is None:
            raise FileError(f"Block {name!r} does not exist.")
        return FileBlock(self._doc, block)


class FileBlock:
    def __init__(self, document: FileDocument, block: Any) -> None:
        self._doc = document
        self.block = block

    @property
    def Name(self) -> str:
        return self.block.name

    @property
    def IsLayout(self) -> bool:
        return bool(self.block.block_record.is_any_layout)

    @property
    def IsXRef(self) -> bool:
        return bool(self.block.block_record.is_xref)

    @property
    def Count(self) -> int:
        return len(self.block)

    def Item(self, index: int) -> FileEntity:
        return self._doc.wrap(list(self.block)[index])

    def __iter__(self) -> Iterator[FileEntity]:
        return (self._doc.wrap(e) for e in list(self.block))


class FileLinetypes:
    def __init__(self, document: FileDocument) -> None:
        self._doc = document

    @property
    def Count(self) -> int:
        return len(self._doc.dxf.linetypes)

    def Item(self, name: str) -> Any:
        if name not in self._doc.dxf.linetypes:
            raise FileError(f"Linetype {name!r} is not loaded.")
        return self._doc.dxf.linetypes.get(name)

    def Load(self, name: str, source: str) -> None:
        from ezdxf.tools.standards import linetypes

        for lt_name, description, pattern in linetypes():
            if lt_name.upper() == name.upper():
                self._doc.dxf.linetypes.add(lt_name, pattern=pattern, description=description)
                return
        raise FileError(f"Linetype {name!r} is not in the standard set.")
