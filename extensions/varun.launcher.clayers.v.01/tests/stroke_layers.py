"""CPU-only USD checks for collapsed stroke layers and active-group membership."""

from __future__ import annotations

from typing import Any, cast

from clayers.model import GROUPS_KEY, ROOT_GROUP, CommunicationLayers, Row
from pxr import Usd, UsdGeom
from tool_box.tools.drawing.plane.selection import DEFAULT_GROUND_PLANE_PATH


class TestContext:
    def __init__(self, stage: Any) -> None:
        self.stage: Any = stage
        self.selected: list[str] = []
        self.pickable: dict[str, bool] = {}

    def get_stage(self) -> Any:
        return self.stage

    def get_selection(self) -> TestContext:
        return self

    def get_selected_prim_paths(self) -> list[str]:
        return self.selected.copy()

    def set_selected_prim_paths(self, paths: list[str], _expand: bool) -> None:
        self.selected = paths.copy()

    def set_pickable(self, path: str, value: bool) -> None:
        self.pickable[path] = value


class TestPlanes:
    def __init__(self) -> None:
        self.active: str = DEFAULT_GROUND_PLANE_PATH

    def set_active_plane(self, path: str) -> None:
        self.active = path

    def active_plane(self) -> str:
        return self.active


def build_model(stage: Any) -> CommunicationLayers:
    model: CommunicationLayers = CommunicationLayers.__new__(CommunicationLayers)
    model.context = TestContext(stage)
    model.stage = None
    model.groups = {}
    model.planes = cast(Any, TestPlanes())
    model._dirty = True
    model._notice = None
    model._strokes = set()
    model._locked = set()
    model._enforcing = False
    model._stage_events = None
    model._updates = None

    def changed() -> None:
        return None

    model._changed = changed
    model._bind_stage(stage)
    model._on_update(None)
    return model


def row_keys(rows: list[Row]) -> set[str]:
    result: set[str] = set()
    row: Row
    for row in rows:
        result.add(row.key)
    return result


def run_checks() -> dict[str, object]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    model: CommunicationLayers = build_model(stage)
    plane: str = "/World/DrawingPlanes/Plane_A"
    cast(Any, UsdGeom.Mesh).Define(stage, plane)
    primary: str = model.create_group("Drawing A", members=[plane])
    model.activate(primary)
    stroke: str
    index: int
    for index in range(100):
        stroke = f"/World/ThreeDDraw/Stroke_{index:03d}"
        cast(Any, UsdGeom.Xform).Define(stage, stroke)
    model._on_update(None)
    layer: str = model.stroke_layer_key(primary)
    collapsed: list[Row] = model.rows(set(model.groups))
    assert plane in row_keys(collapsed) and layer in row_keys(collapsed)
    assert not any(row.kind == "Stroke" for row in collapsed)
    expanded: list[Row] = model.rows(set(model.groups) | {layer})
    assert len(model.stroke_paths(primary)) == 100
    assert sum(row.kind == "Stroke" for row in expanded) == 100
    row: Row
    for row in expanded:
        if row.kind == "Stroke":
            assert row.depth == 2 and model.group_for(row.key) == primary
    assert model.row_paths(layer) == model.stroke_paths(primary)
    assert plane not in model.row_paths(layer)
    original_paths: list[str] = model.paths("/World/ThreeDDraw")
    model.select(model.row_paths(layer))
    assert len(model.selected_paths()) == 100
    model.toggle_lock(layer)
    model._on_update(None)
    assert model.locked(layer) and model.locked(original_paths[0])
    assert model.selected_paths() == [] and not model.locked(plane)
    model.set_visible(layer, False)
    assert not model.visible(layer) and not model.visible(original_paths[0])
    assert model.visible(plane)
    new_path: str = "/World/ThreeDDraw/Stroke_100"
    cast(Any, UsdGeom.Xform).Define(stage, new_path)
    model._on_update(None)
    assert model.group_for(new_path) == primary and model.locked(new_path) and not model.visible(new_path)
    model.set_visible(primary, False)
    model.set_visible(primary, True)
    assert not model.visible(new_path), "Parent visibility overwrote the stroke layer setting"
    model.toggle_lock(layer)
    assert not model.locked(new_path)
    assert model.visible(plane) and model.paths("/World/ThreeDDraw") == [*original_paths, new_path]

    # Active nested groups receive new strokes in their own layer.
    second: str = model.create_group("Drawing B")
    model.activate(second)
    nested: str = model.create_group("Details", parent=second, active=True)
    nested_path: str = "/World/ThreeDDraw/Nested"
    cast(Any, UsdGeom.Xform).Define(stage, nested_path)
    model._on_update(None)
    assert model.group_for(nested_path) == nested
    nested_layer: str = model.stroke_layer_key(nested)
    assert model.row_paths(nested_layer) == [nested_path]
    model.activate(nested_layer)
    assert model.planes.active_plane() == DEFAULT_GROUND_PLANE_PATH

    # Layer preferences persist with existing group metadata and old files keep defaults.
    model.toggle_lock(layer)
    saved: str = stage.GetRootLayer().ExportToString()
    reopened: Any = cast(Any, Usd.Stage).CreateInMemory()
    assert reopened.GetRootLayer().ImportFromString(saved)
    restored: CommunicationLayers = build_model(reopened)
    assert restored.groups[primary].stroke_locked and not restored.groups[primary].stroke_visible
    assert restored.group_for(new_path) == primary and restored.locked(new_path)
    assert plane in row_keys(restored.rows(set(restored.groups)))
    legacy: Any = cast(Any, Usd.Stage).CreateInMemory()
    legacy.GetRootLayer().customLayerData = {GROUPS_KEY: {"g0": {"name": "Legacy", "active": True}}}
    legacy_model: CommunicationLayers = build_model(legacy)
    assert legacy_model.groups["g0"].stroke_visible and not legacy_model.groups["g0"].stroke_locked
    legacy_path: str = "/World/ThreeDDraw/Unassigned"
    cast(Any, UsdGeom.Xform).Define(legacy, legacy_path)
    legacy_model._strokes.add(legacy_path)
    legacy_model._on_update(None)
    root_layer: str = legacy_model.stroke_layer_key(ROOT_GROUP)
    assert root_layer in row_keys(legacy_model.rows(set(legacy_model.groups)))
    assert legacy_path not in row_keys(legacy_model.rows(set(legacy_model.groups)))
    assert legacy_model.row_paths(root_layer) == [legacy_path]
    assert legacy_path in row_keys(legacy_model.rows({root_layer}))
    model._notice.Revoke()
    restored._notice.Revoke()
    legacy_model._notice.Revoke()
    return {
        "passed": True,
        "strokes_collapsed": 101,
        "nested_active_group": True,
        "visibility_and_lock_preserved": True,
        "usd_paths_unchanged": True,
        "legacy_and_roundtrip": True,
    }
