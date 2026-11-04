"""Viewport brush cursor, canvas projection, and drawing-method gestures."""

from __future__ import annotations

import logging
import math
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any, TypeAlias, TypedDict, cast

import carb.input
import numpy as np
import omni.ui
from omni.ui import scene as sc
from pxr import Gf

from tool_box.core.viewport import get_active_stage
from tool_box.tools.drawing.brush import BrushConfig, BrushSample
from tool_box.tools.drawing.canvas import surface_hit
from tool_box.tools.drawing.methods import (
    AXES,
    AxisIndex,
    CursorAnchor,
    DrawingMethods,
    Vec2,
    Vec3,
    axis_plane_normal,
    axis_point_from_ray,
    choose_axis,
    intersect_plane,
)
from tool_box.tools.drawing.pen import PenInput
from tool_box.tools.drawing.pipe_targets import PipeTargets
from tool_box.tools.drawing.plane.geometry import PlaneRegistry
from tool_box.tools.drawing.plane.selection import (
    DEFAULT_GROUND_PLANE_PATH,
    PlaneSelection,
)
from tool_box.tools.drawing.settings import CURSOR_FACTORY_ID, RING_COLOR
from tool_box.tools.drawing.shapes import outline
from tool_box.tools.drawing.surface import FloatArray, SurfaceHit

LOGGER: logging.Logger = logging.getLogger(__name__)
WorldPos: TypeAlias = Vec3
DragCallback: TypeAlias = Callable[[WorldPos], None]
ADJUST_MODIFIERS: int = carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL | carb.input.KEYBOARD_MODIFIER_FLAG_ALT
AXIS_COLORS: tuple[int, int, int] = (0xFF7777FF, 0xFF77DD77, 0xFFFFAA66)


class _Listener(TypedDict):
    begin: DragCallback | None
    change: DragCallback | None
    end: Callable[[], None] | None


_listener: _Listener | None = None
_active_scene: _CursorScene | None = None
_scenes: set[_CursorScene] = set()


def _vec(value: Any) -> Vec3:
    return float(value[0]), float(value[1]), float(value[2])


def _translation(position: Vec3) -> list[float]:
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, *position, 1.0]


def _basis_for_normal(normal: Gf.Vec3d) -> tuple[Gf.Vec3d, Gf.Vec3d]:
    reference: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0) if abs(_vec(normal)[1]) < 0.9 else Gf.Vec3d(1.0, 0.0, 0.0)
    right: Any = cast(Any, Gf).Cross(normal, reference)
    length: float = float(right.GetLength())
    if length < 1e-9:
        return Gf.Vec3d(1.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0)
    right = right / length
    up: Any = cast(Any, Gf).Cross(normal, right)
    return right, up


class Cursor3D:
    def __init__(self) -> None:
        self._registration: Any = None

    def show(self) -> None:
        if self._registration is not None:
            return
        from omni.kit.viewport.registry import RegisterScene

        PenInput.get().start()
        self._registration = cast(Any, RegisterScene)(_CursorScene, CURSOR_FACTORY_ID)
        scene: _CursorScene
        for scene in tuple(_scenes):
            scene.set_visible(True)
            scene.refresh_methods()

    def hide(self) -> None:
        self.finish_gestures()
        PenInput.get().stop()
        scene: _CursorScene
        for scene in tuple(_scenes):
            scene.set_visible(False)
        if self._registration is not None:
            self._registration.destroy()
            self._registration = None

    @staticmethod
    def get_scene() -> _CursorScene | None:
        return _active_scene

    def set_listener(
        self,
        on_begin: DragCallback | None = None,
        on_change: DragCallback | None = None,
        on_end: Callable[[], None] | None = None,
    ) -> None:
        global _listener
        _listener = {"begin": on_begin, "change": on_change, "end": on_end}

    def clear_listener(self) -> None:
        global _listener
        _listener = None

    @staticmethod
    def sample() -> BrushSample:
        return BrushConfig.get().sample(PenInput.get().pressure)

    def refresh(self) -> None:
        scene: _CursorScene
        for scene in tuple(_scenes):
            scene.refresh_brush()
            scene.refresh_methods()

    @staticmethod
    def refresh_methods() -> None:
        scene: _CursorScene
        for scene in tuple(_scenes):
            scene.refresh_methods()

    @staticmethod
    def finish_gestures() -> None:
        scene: _CursorScene
        gesture: _Drag
        for scene in tuple(_scenes):
            for gesture in scene._drags:
                gesture.finish(suppress=True)

    @staticmethod
    def gesture_active() -> bool:
        scene: _CursorScene
        gesture: _Drag
        for scene in _scenes:
            for gesture in scene._drags:
                if gesture._mode:
                    return True
        return False

    @staticmethod
    def radius() -> float:
        return BrushConfig.get().brush_radius


class _CursorScene:
    def __init__(self, desc: dict[str, Any]) -> None:
        global _active_scene
        self._viewport_api: Any = desc.get("viewport_api")
        self._up_axis: str = self._detect_up_axis()
        self._world_pos: Vec3 | None = None
        self._last_ndc: Vec2 | None = None
        self._visible: bool = True
        self._brush_signature: tuple[float, str] | None = None
        self._brush_outline: Any = None
        self._origin: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 0.0)
        self._normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        self._basis_r: Gf.Vec3d = Gf.Vec3d(1.0, 0.0, 0.0)
        self._basis_u: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0)
        self._frame_signature: tuple[Vec3, Vec3] | None = None
        self._orient_transform: Any = None
        self._method_signature: tuple[object, ...] | None = None
        self._stroke_active: bool = False
        self._axis_start: Vec3 | None = None
        self._axis_index: AxisIndex | None = None
        self.surface_path: str | None = None
        self.draw_on_surface: bool = False
        self.projection_normal: Vec3 = (0.0, 1.0, 0.0)
        self._pipe_targets: PipeTargets | None = None
        self._pipe_stage: Any = None
        self._pipe_hover: bool = False
        self._spokes: list[Any] = []
        self._anchor_lines: list[Any] = []
        self._drags: list[_Drag] = []
        self._layer_listener_cb: Callable[[], None] = self._on_active_plane_changed
        self._refresh_active_plane()
        _active_scene = self
        _scenes.add(self)
        self._root: Any = sc.Transform(visible=True)
        with self._root:
            self._transform: Any = sc.Transform()
            with self._transform:
                self._orient_transform = sc.Transform(transform=self._compute_orient_matrix())
                with self._orient_transform:
                    self._brush_outline = sc.Curve([(0, 0, 0), (1, 0, 0)])
                    self.refresh_brush()
                self._spoke_transform: Any = sc.Transform(visible=False)
                with self._spoke_transform:
                    color: int
                    for color in AXIS_COLORS:
                        self._spokes.append(sc.Line((0, 0, 0), (1, 0, 0), color=color, thickness=1.5))
            self._anchor_transform: Any = sc.Transform(visible=False)
            with self._anchor_transform:
                for color in AXIS_COLORS:
                    self._anchor_lines.append(sc.Line((0, 0, 0), (1, 0, 0), color=color, thickness=2.5))
            self._hover_screen: Any = sc.Screen(gesture=_Hover(self))
            self._drags = [_Drag(self), _Drag(self, ADJUST_MODIFIERS)]
            self._drag_screen: Any = sc.Screen(gestures=self._drags)
        registry: PlaneSelection | None = PlaneSelection.get()
        if registry is not None:
            registry.add_listener(self._layer_listener_cb)
        self.refresh_methods()

    @property
    def categories(self) -> tuple[str, ...]:
        return ("manipulator",)

    @property
    def name(self) -> str:
        return "ThreeDDrawCursor"

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self.set_visible(value)

    def set_visible(self, value: bool) -> None:
        self._visible = bool(value)
        if not value and self._pipe_targets is not None:
            self._pipe_targets.reset()
        if self._root is not None:
            self._root.visible = self._visible

    def destroy(self) -> None:
        global _active_scene
        if self._pipe_targets is not None:
            self._pipe_targets.destroy()
            self._pipe_targets = None
        gesture: _Drag
        for gesture in self._drags:
            gesture.finish(suppress=True)
        registry: PlaneSelection | None = PlaneSelection.get()
        if registry is not None:
            registry.remove_listener(self._layer_listener_cb)
        _scenes.discard(self)
        if _active_scene is self:
            _active_scene = None
        self._transform = self._orient_transform = self._root = None

    @property
    def viewport_api(self) -> Any:
        return self._viewport_api

    @property
    def anchor(self) -> CursorAnchor:
        stage: Any = get_active_stage(self._viewport_api)
        key: str = str(stage.GetRootLayer().identifier) if stage is not None else ""
        return CursorAnchor.get(key)

    @property
    def methods(self) -> DrawingMethods:
        return DrawingMethods.get(BrushConfig.get().current_tool)

    def _detect_up_axis(self) -> str:
        from pxr import UsdGeom

        stage: Any = get_active_stage(self._viewport_api)
        return str(cast(Any, UsdGeom).GetStageUpAxis(stage)) if stage is not None else "Y"

    def _active_plane_info(self) -> tuple[Gf.Vec3d, Gf.Vec3d]:
        anchor: CursorAnchor = self.anchor
        if self.methods.cursor_3d and not anchor.placing:
            plane: tuple[Vec3, Vec3] | None = anchor.plane()
            if plane is not None:
                return Gf.Vec3d(*plane[0]), Gf.Vec3d(*plane[1])
        registry: PlaneSelection | None = PlaneSelection.get()
        if self.methods.drawing_plane and registry is not None:
            path: str = registry.active_plane(self._viewport_api)
            if path != DEFAULT_GROUND_PLANE_PATH:
                info: tuple[Gf.Vec3d, Gf.Vec3d] | None = PlaneRegistry.info_for_path(path, self._viewport_api)
                if info is not None:
                    return info
        normal: Gf.Vec3d = Gf.Vec3d(0, 0, 1) if self._up_axis == "Z" else Gf.Vec3d(0, 1, 0)
        return Gf.Vec3d(0, 0, 0), normal

    def _set_frame(self, origin: Vec3, normal: Vec3) -> None:
        if (origin, normal) == self._frame_signature:
            return
        self._frame_signature = (origin, normal)
        length: float = math.sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2])
        nrm: Any = Gf.Vec3d(*normal) / max(length, 1e-9)
        self._origin = Gf.Vec3d(*origin)
        self._normal = nrm
        self._basis_r, self._basis_u = _basis_for_normal(nrm)
        if self._orient_transform is not None:
            self._orient_transform.transform = self._compute_orient_matrix()

    def _refresh_active_plane(self) -> None:
        origin: Gf.Vec3d
        normal: Gf.Vec3d
        origin, normal = self._active_plane_info()
        self._set_frame(_vec(origin), _vec(normal))

    def _compute_orient_matrix(self) -> list[float]:
        return [*_vec(self._basis_r), 0.0, *_vec(self._basis_u), 0.0, *_vec(self._normal), 0.0, 0.0, 0.0, 0.0, 1.0]

    def _on_active_plane_changed(self) -> None:
        Cursor3D.finish_gestures()
        self._method_signature = None
        self.refresh_methods()

    def refresh_methods(self) -> None:
        anchor: CursorAnchor = self.anchor
        profile: DrawingMethods = self.methods
        if not profile.axes_spokes and self._pipe_targets is not None:
            self._pipe_targets.reset()
            self._pipe_hover = False
        signature: tuple[object, ...] = (
            id(anchor),
            BrushConfig.get().current_tool,
            profile.drawing_plane,
            profile.draw_on_object,
            profile.axes_spokes,
            profile.cursor_3d,
            anchor.position,
            anchor.axis,
            anchor.placing,
        )
        if signature != self._method_signature and not self._stroke_active:
            self._method_signature = signature
            self._up_axis = self._detect_up_axis()
            self._refresh_active_plane()
            self.surface_path = None
            self.draw_on_surface = False
        self._spoke_transform.visible = profile.axes_spokes
        self._anchor_transform.visible = profile.cursor_3d and anchor.position is not None
        interactive: bool = BrushConfig.get().active or anchor.placing or Cursor3D.gesture_active()
        self._drag_screen.visible = interactive
        self._hover_screen.visible = interactive
        if not interactive:
            self._transform.visible = False
        if anchor.position is not None:
            self._anchor_transform.transform = _translation(anchor.position)
            self._update_lines(self._anchor_lines, self._visual_span(anchor.position, 12.0))

    @staticmethod
    def _update_lines(lines: list[Any], span: float) -> None:
        index: int
        line: Any
        for index, line in enumerate(lines):
            axis: Vec3 = AXES[index]
            line.start = (-axis[0] * span, -axis[1] * span, -axis[2] * span)
            line.end = (axis[0] * span, axis[1] * span, axis[2] * span)

    def _visual_span(self, position: Vec3, pixels: float) -> float:
        projected: Vec2 | None = self._project(position)
        if projected is None:
            return max(Cursor3D.radius(), 0.1)
        resolution: Any = self._viewport_api.resolution
        scale: float = pixels * max(1.0, omni.ui.Workspace.get_dpi_scale()) * 2.0 / max(1.0, float(resolution[0]))
        ray: tuple[Vec3, Vec3] | None = self._ray((projected[0] + scale, projected[1]))
        center_ray: tuple[Vec3, Vec3] | None = self._ray(projected)
        if ray is None or center_ray is None:
            return max(Cursor3D.radius(), 0.1)
        edge: Vec3 | None = intersect_plane(ray[0], ray[1], position, center_ray[1])
        return max(math.dist(position, edge), 1e-5) if edge is not None else max(Cursor3D.radius(), 0.1)

    def update(self, mouse_ndc: Vec2) -> Vec3 | None:
        if self._transform is None or self._viewport_api is None:
            return None
        self.refresh_brush()
        self.refresh_methods()
        position: Vec3 | None = self._raycast(mouse_ndc)
        self._transform.visible = position is not None
        if position is None:
            return None
        self._world_pos = position
        visual: Vec3 = position
        last: Vec2 | None = self._last_ndc
        # Keep the established one-frame cursor lead only for planar drawing.
        if (
            last is not None
            and not self.anchor.placing
            and not self.draw_on_surface
            and not self._pipe_hover
            and self._axis_index is None
        ):
            predicted: Vec2 = (2.0 * mouse_ndc[0] - last[0], 2.0 * mouse_ndc[1] - last[1])
            ray: tuple[Vec3, Vec3] | None = self._ray(predicted)
            if ray is not None:
                future: Vec3 | None = intersect_plane(ray[0], ray[1], _vec(self._origin), _vec(self._normal))
                if future is not None:
                    visual = future
        self._last_ndc = mouse_ndc
        self._transform.transform = _translation(visual)
        if self.methods.axes_spokes:
            self._update_lines(self._spokes, self._visual_span(position, 45.0))
        return position

    def refresh_brush(self) -> None:
        if self._brush_outline is None:
            return
        config: BrushConfig = BrushConfig.get()
        radius: float = Cursor3D.sample().radius
        signature: tuple[float, str] = (radius, config.shape)
        if signature == self._brush_signature:
            return
        self._brush_signature = signature
        positions: list[Vec3] = []
        vertex: Any
        for vertex in outline(config.shape):
            positions.append((float(vertex[0]) * radius, float(vertex[1]) * radius, 0.0))
        positions.append(positions[0])
        self._brush_outline.positions = positions
        self._brush_outline.colors = [RING_COLOR] * len(positions)
        self._brush_outline.thicknesses = [2.0] * len(positions)

    @property
    def world_pos(self) -> Vec3 | None:
        return self._world_pos

    @property
    def up_axis(self) -> str:
        return self._up_axis

    def _ray(self, mouse_ndc: Vec2) -> tuple[Vec3, Vec3] | None:
        matrix: Any = getattr(self._viewport_api, "ndc_to_world", None)
        if matrix is None:
            return None
        near: Vec3 = _vec(matrix.Transform(Gf.Vec3d(mouse_ndc[0], mouse_ndc[1], -1.0)))
        far: Vec3 = _vec(matrix.Transform(Gf.Vec3d(mouse_ndc[0], mouse_ndc[1], 1.0)))
        return near, (far[0] - near[0], far[1] - near[1], far[2] - near[2])

    def _project(self, position: Vec3) -> Vec2 | None:
        matrix: Any = getattr(self._viewport_api, "ndc_to_world", None)
        if matrix is None:
            return None
        projected: Any = matrix.GetInverse().Transform(Gf.Vec3d(*position))
        return float(projected[0]), float(projected[1])

    def _raycast(self, mouse_ndc: Vec2) -> Vec3 | None:
        if self.methods.cursor_3d and self.anchor.placing:
            self._pipe_hover = False
            return self._placement_raycast(mouse_ndc)
        position: Vec3 | None = self._canvas_raycast(mouse_ndc)
        if not self.methods.axes_spokes or self._stroke_active:
            return position
        stage: Any = get_active_stage(self._viewport_api)
        if stage is None:
            return position
        if self._pipe_targets is None or self._pipe_stage != stage:
            if self._pipe_targets is not None:
                self._pipe_targets.destroy()
            self._pipe_stage = stage
            self._pipe_targets = PipeTargets(stage)
        # The projected brush circle supplies the pick footprint, including its tilt.
        point: Vec3 | None = position
        if point is None:
            ray: tuple[Vec3, Vec3] | None = self._ray(mouse_ndc)
            if ray is not None:
                point = intersect_plane(ray[0], ray[1], _vec(self._origin), _vec(self._normal))
        metric: FloatArray = np.eye(2, dtype=np.float64) * 100.0
        if point is not None:
            center: Vec2 | None = self._project(point)
            right: Vec3 = _vec(self._basis_r)
            up: Vec3 = _vec(self._basis_u)
            radius: float = Cursor3D.sample().radius
            horizontal: Vec2 | None = self._project(
                (point[0] + right[0] * radius, point[1] + right[1] * radius, point[2] + right[2] * radius)
            )
            vertical: Vec2 | None = self._project(
                (point[0] + up[0] * radius, point[1] + up[1] * radius, point[2] + up[2] * radius)
            )
            if center is not None and horizontal is not None and vertical is not None:
                basis: FloatArray = np.asarray((horizontal, vertical), dtype=np.float64) - center
                if abs(float(np.linalg.det(basis))) > 1e-12:
                    metric = np.asarray(np.linalg.inv(basis), dtype=np.float64)
        hit: SurfaceHit | None = self._pipe_targets.hit(mouse_ndc, self._viewport_api.ndc_to_world, metric)
        if hit is not None:
            projected: Vec2 | None = self._project(hit.position)
            sight: tuple[Vec3, Vec3] | None = self._ray(projected) if projected is not None else None
            if sight is not None:
                occluder: SurfaceHit | None = surface_hit(self._viewport_api, sight[0], sight[1])
                if occluder is not None and occluder.distance < hit.distance - 1e-4:
                    hit = None
        self._pipe_targets.recognize(hit)
        self._pipe_hover = hit is not None
        return hit.position if hit is not None else position

    # Place on the nearest enabled canvas hit; snap only the ground-grid fallback.
    def _placement_raycast(self, mouse_ndc: Vec2) -> Vec3 | None:
        ray: tuple[Vec3, Vec3] | None = self._ray(mouse_ndc)
        if ray is None:
            return None
        profile: DrawingMethods = self.methods
        plane_info: tuple[Gf.Vec3d, Gf.Vec3d] | None = None
        plane_point: Vec3 | None = None
        if profile.drawing_plane:
            path: str = PlaneSelection.get().active_plane(self._viewport_api)
            if path != DEFAULT_GROUND_PLANE_PATH:
                plane_info = PlaneRegistry.info_for_path(path, self._viewport_api)
                if plane_info is not None:
                    plane_point = intersect_plane(ray[0], ray[1], _vec(plane_info[0]), _vec(plane_info[1]))
        hit: SurfaceHit | None = surface_hit(self._viewport_api, ray[0], ray[1]) if profile.draw_on_object else None
        if hit is not None and (plane_point is None or hit.distance <= math.dist(ray[0], plane_point)):
            self.surface_path = hit.path
            self.draw_on_surface = True
            self._set_frame(hit.position, hit.normal)
            return hit.position
        self.surface_path = None
        self.draw_on_surface = False
        if plane_point is not None and plane_info is not None:
            self._set_frame(_vec(plane_info[0]), _vec(plane_info[1]))
            return plane_point
        normal: Vec3 = (0.0, 0.0, 1.0) if self._up_axis == "Z" else (0.0, 1.0, 0.0)
        self._set_frame((0.0, 0.0, 0.0), normal)
        position: Vec3 | None = intersect_plane(ray[0], ray[1], (0.0, 0.0, 0.0), normal)
        grid_module: ModuleType | None = sys.modules.get("varun.launcher.usd_viewport.v.01.viewport_light.grid")
        if position is not None and grid_module is not None:
            snap: Callable[[Any, Vec3], Vec3] = grid_module.snap_cursor_to_grid
            return snap(self._viewport_api, position)
        return position

    def _canvas_raycast(self, mouse_ndc: Vec2) -> Vec3 | None:
        ray: tuple[Vec3, Vec3] | None = self._ray(mouse_ndc)
        if ray is None:
            return None
        if self._axis_start is not None and self._axis_index is not None:
            return axis_point_from_ray(self._axis_start, self._axis_index, ray[0], ray[1])
        anchor: CursorAnchor = self.anchor
        profile: DrawingMethods = self.methods
        cursor_plane: bool = profile.cursor_3d and anchor.position is not None and not anchor.placing
        if not profile.draw_on_object or cursor_plane:
            if not self._stroke_active:
                self.surface_path = None
                self.draw_on_surface = False
                self._refresh_active_plane()
            return intersect_plane(ray[0], ray[1], _vec(self._origin), _vec(self._normal))
        hit: Any = surface_hit(self._viewport_api, ray[0], ray[1])
        # A stroke keeps its construction frame and object until it ends.
        if self._stroke_active:
            if self.draw_on_surface:
                return hit.position if hit is not None and hit.path == self.surface_path else None
            return intersect_plane(ray[0], ray[1], _vec(self._origin), _vec(self._normal))
        plane_origin: Gf.Vec3d
        plane_normal: Gf.Vec3d
        plane_origin, plane_normal = self._active_plane_info()
        plane_point: Vec3 | None = (
            intersect_plane(ray[0], ray[1], _vec(plane_origin), _vec(plane_normal)) if profile.drawing_plane else None
        )
        if hit is not None and (
            plane_point is None or math.dist(ray[0], hit.position) <= math.dist(ray[0], plane_point)
        ):
            self.surface_path = hit.path
            self.draw_on_surface = True
            length: float = math.sqrt(ray[1][0] ** 2 + ray[1][1] ** 2 + ray[1][2] ** 2)
            normal: Vec3 = (-ray[1][0] / length, -ray[1][1] / length, -ray[1][2] / length)
            self.projection_normal = normal
            self._set_frame(hit.position, normal)
            return hit.position
        self.surface_path = None
        self.draw_on_surface = False
        if plane_point is not None:
            self._set_frame(_vec(plane_origin), _vec(plane_normal))
        return plane_point

    def begin_stroke(self) -> None:
        self._stroke_active = True

    def end_stroke(self) -> None:
        self._stroke_active = False
        self._axis_index = None
        self._axis_start = None
        self._last_ndc = None
        self._method_signature = None
        self.refresh_methods()

    def lock_axis(self, start: Vec3, start_ndc: Vec2, current_ndc: Vec2) -> bool:
        resolution: Any = self._viewport_api.resolution
        delta: Vec2 = (
            (current_ndc[0] - start_ndc[0]) * float(resolution[0]),
            (current_ndc[1] - start_ndc[1]) * float(resolution[1]),
        )
        dpi: float = max(1.0, omni.ui.Workspace.get_dpi_scale())
        if math.hypot(*delta) < 6.0 * dpi:
            return False
        projected_start: Vec2 | None = self._project(start)
        ray: tuple[Vec3, Vec3] | None = self._ray(start_ndc)
        if projected_start is None or ray is None:
            return False
        span: float = self._visual_span(start, 45.0)
        projected_axes: list[Vec2 | None] = []
        axis: Vec3
        for axis in AXES:
            endpoint: Vec3 = (start[0] + axis[0] * span, start[1] + axis[1] * span, start[2] + axis[2] * span)
            projected: Vec2 | None = self._project(endpoint)
            projected_axes.append(
                None
                if projected is None
                else (
                    (projected[0] - projected_start[0]) * float(resolution[0]),
                    (projected[1] - projected_start[1]) * float(resolution[1]),
                )
            )
        selected: AxisIndex | None = choose_axis(delta, (projected_axes[0], projected_axes[1], projected_axes[2]))
        if selected is None:
            return False
        self._axis_start = start
        self._axis_index = selected
        self.draw_on_surface = False
        self.surface_path = None
        self._set_frame(start, axis_plane_normal(selected, ray[1]))
        return True


class _Hover(sc.HoverGesture):
    def __init__(self, scene: _CursorScene) -> None:
        cast(Any, super()).__init__()
        self._scene: _CursorScene = scene

    def on_changed(self) -> None:
        if self._scene.visible:
            mouse: Any = self.sender.gesture_payload.mouse
            self._scene.update((float(mouse[0]), float(mouse[1])))


class _Drag(sc.DragGesture):
    def __init__(self, scene: _CursorScene, modifiers: int = 0) -> None:
        cast(Any, super()).__init__(modifiers=modifiers)
        self._scene: _CursorScene = scene
        self._mode: str = ""
        self._last_mouse: Vec2 | None = None
        self._start_mouse: Vec2 | None = None
        self._start: Vec3 | None = None
        self._suppressed: bool = False

    def on_began(self) -> None:
        global _active_scene
        _active_scene = self._scene
        self.finish()
        self._suppressed = False
        self._route()

    def on_changed(self) -> None:
        self._route()

    def _emit(self, event: str, position: Vec3) -> None:
        callback: DragCallback | None = _listener["begin" if event == "begin" else "change"] if _listener else None
        if callback is not None:
            callback(position)

    def _route(self) -> None:
        if not self._scene.visible or self._suppressed or self._mode == "place":
            return
        # Touch is reserved for viewport navigation; only a pen or mouse can draw, place, or adjust.
        if PenInput.get().touch:
            return
        try:
            mouse: Any = self.sender.gesture_payload.mouse
            ndc: Vec2 = (float(mouse[0]), float(mouse[1]))
            flags: int = carb.input.acquire_input_interface().get_modifier_flags(
                device_types=[carb.input.DeviceType.KEYBOARD]
            )
            adjusting: bool = BrushConfig.get().active and flags & ADJUST_MODIFIERS == ADJUST_MODIFIERS
            if adjusting:
                if self._mode in ("draw", "pending"):
                    self.finish()
                if self._mode == "adjust" and self._last_mouse is not None:
                    resolution: Any = self._scene.viewport_api.resolution
                    dpi: float = max(1.0, omni.ui.Workspace.get_dpi_scale())
                    BrushConfig.get().adjust(
                        (ndc[0] - self._last_mouse[0]) * float(resolution[0]) / (2 * dpi),
                        -(ndc[1] - self._last_mouse[1]) * float(resolution[1]) / (2 * dpi),
                    )
                self._mode = "adjust"
                self._scene.update(ndc)
                self._last_mouse = ndc
                return
            position: Vec3 | None = self._scene.update(ndc)
            if self._scene.methods.cursor_3d and self._scene.anchor.placing:
                self._mode = "place"
                if position is not None:
                    self._scene.anchor.place(position)
                    self._scene.refresh_methods()
            elif not BrushConfig.get().active:
                return
            elif self._mode == "pending":
                if (
                    self._start is not None
                    and self._start_mouse is not None
                    and self._scene.lock_axis(self._start, self._start_mouse, ndc)
                ):
                    self._scene.begin_stroke()
                    self._emit("begin", self._start)
                    self._mode = "draw"
                    constrained: Vec3 | None = self._scene.update(ndc)
                    if constrained is not None:
                        self._emit("change", constrained)
            elif position is None and self._mode == "draw":
                self.finish()
            elif position is not None and (self._mode != "draw" or ndc != self._last_mouse):
                if self._mode != "draw":
                    if self._scene.methods.axes_spokes:
                        self._start, self._start_mouse = position, ndc
                        self._scene.begin_stroke()
                        self._mode = "pending"
                    else:
                        self._scene.begin_stroke()
                        self._emit("begin", position)
                        self._mode = "draw"
                else:
                    self._emit("change", position)
            self._last_mouse = ndc
        except Exception:
            LOGGER.exception("Could not process brush gesture")

    def finish(self, suppress: bool = False) -> None:
        if self._mode == "pending" and self._start is not None and not suppress:
            self._emit("begin", self._start)
            self._mode = "draw"
        if self._mode == "draw":
            callback: Callable[[], None] | None = _listener["end"] if _listener else None
            if callback is not None:
                callback()
        self._mode = ""
        self._last_mouse = self._start_mouse = None
        self._start = None
        self._suppressed = suppress
        self._scene.end_stroke()

    def on_ended(self) -> None:
        self.finish()
