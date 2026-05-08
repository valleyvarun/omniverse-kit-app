from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import carb
import carb.input
import carb.settings
import omni.appwindow
import omni.kit.app
import omni.usd

from .cursor_3d import Cursor3D
from .tool import Tool


LOGGER = logging.getLogger(__name__)


# Tool icon directory.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "logos"

# Viewport right-click context menu setting key.
_VIEWPORT_CONTEXT_MENU_SETTING = "/exts/omni.kit.window.viewport/showContextMenu"

# Sentinel for "not captured".
_UNSET = object()

# Drawing constants.
_SPHERE_RADIUS = 0.2
_SPHERE_COLOR = (0.0, 0.0, 0.0)
_EMIT_INTERVAL = 0.016
_STAMP_SPACING = 2.0
_DRAW_ROOT = "/World/ThreeDDraw"


# RESAMPLE A POLYLINE INTO UNIFORMLY SPACED STAMPS ALONG ITS ARC LENGTH.
def _resample_polyline(
    polyline: list[tuple[float, float, float]],
    spacing: float,
    carry: float,
    seed_first: bool,
) -> tuple[list[tuple[float, float, float]], tuple[float, float, float], float]:
    stamps: list[tuple[float, float, float]] = []
    # Optionally seed the very first stamp at the polyline start.
    if seed_first and polyline:
        stamps.append(polyline[0])
        carry = 0.0
    # Distance walked since the last stamp (carries across calls).
    dist_since = carry
    # Walk each segment, emitting stamps every `spacing` units.
    for i in range(len(polyline) - 1):
        ax, ay, az = polyline[i]
        bx, by, bz = polyline[i + 1]
        dx, dy, dz = bx - ax, by - ay, bz - az
        seg_len = (dx * dx + dy * dy + dz * dz) ** 0.5
        # Skip degenerate segments.
        if seg_len <= 1e-9:
            continue
        remaining = seg_len
        # Drop stamps along this segment until the next one would overshoot.
        while dist_since + remaining >= spacing - 1e-9:
            advance = spacing - dist_since
            offset = (seg_len - remaining) + advance
            t = offset / seg_len
            stamps.append((ax + dx * t, ay + dy * t, az + dz * t))
            remaining -= advance
            dist_since = 0.0
        dist_since += remaining
    # Track the last stamp position for spacing continuity across ticks.
    last_stamp = stamps[-1] if stamps else (polyline[-1] if polyline else (0.0, 0.0, 0.0))
    return stamps, last_stamp, dist_since


# FREEHAND 3D DRAWING TOOL.
class ThreeDDrawTool:
    NAME = "3D Draw"
    LABEL = "3D"
    SHORTCUT = "D"
    TOOLTIP = "Draw freehand strokes in 3D."
    ICON_NAME = "3d_draw.svg"

    # INITIALIZE TOOL STATE AND REGISTER THE PROTOTYPE-DELETION LISTENER.
    def __init__(self) -> None:
        self._tool: Tool | None = None
        self._is_active: bool = False
        # Esc key listener handle.
        self._keyboard_sub: int | None = None
        # Viewport layers hidden while active, with their prior visibility.
        self._hidden_layers: list[tuple[Any, bool]] = []
        # Saved viewport context-menu setting.
        self._prev_context_menu: Any = _UNSET
        # 3D cursor that follows the active drawing plane (or the ground
        # plane when none is active in CLayers).
        self._cursor: Cursor3D | None = None
        # Active stroke state.
        self._drawing = False
        self._draw_pos: tuple[float, float, float] | None = None
        # Raw cursor samples for the current stroke, accumulated between ticks.
        self._stroke_samples: list[tuple[float, float, float]] = []
        # Last position where a stamp was actually emitted.
        self._last_stamp_pos: tuple[float, float, float] | None = None
        # Carried arc length between ticks for uniform spacing.
        self._stamp_carry = 0.0
        self._accum = 0.0
        self._update_sub: Any = None
        # PointInstancer path for the current stroke (one per LMB drag).
        self._stroke_path: str | None = None
        # Tf listener that cleans up parent stroke prims when prototype is deleted.
        self._notice_listener: Any = None
        self._register_proto_listener()

    # BUILD THE TOOLBAR TOOL DESCRIPTOR.
    def make_tool(self) -> Tool:
        icon_path = _ICONS_DIR / self.ICON_NAME
        tool = Tool(
            name=self.NAME,
            icon=str(icon_path) if icon_path.exists() else None,
            icon_text=None if icon_path.exists() else self.LABEL,
            shortcut=self.SHORTCUT,
            tooltip=self.TOOLTIP,
            on_click=self._on_clicked,
            toggleable=True,
        )
        self._tool = tool
        return tool

    # CLEANUP ON SHUTDOWN.
    def destroy(self) -> None:
        self.deactivate()
        if self._notice_listener is not None:
            try:
                self._notice_listener.Revoke()
            except Exception:
                pass
            self._notice_listener = None
        self._tool = None

    # TOGGLE THE TOOL.
    def _on_clicked(self) -> None:
        if self._is_active:
            self.deactivate()
        else:
            self.activate()

    # ACTIVATE THE TOOL: CLEAR SELECTION, SUPPRESS VIEWPORT INTERACTIONS, SHOW CURSOR.
    def activate(self) -> None:
        if self._is_active:
            return
        self._is_active = True
        if self._tool is not None:
            self._tool.set_active(True)
        self._clear_selection()
        self._subscribe_keyboard()
        self._disable_viewport_interactions()
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )

    # DEACTIVATE THE TOOL AND RESTORE VIEWPORT STATE.
    def deactivate(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        if self._tool is not None:
            self._tool.set_active(False)
        self._unsubscribe_keyboard()
        self._restore_viewport_interactions()
        self._stop_emitting()
        self._drawing = False
        if self._cursor is not None:
            self._cursor.clear_listener()
            self._cursor.hide()
            self._cursor = None

    # CLEAR THE CURRENT VIEWPORT SELECTION.
    def _clear_selection(self) -> None:
        try:
            usd_context = cast(Any, omni.usd.get_context())
            selection = usd_context.get_selection()
            selection.clear_selected_prim_paths()
        except Exception as exc:
            LOGGER.warning("3D Draw: could not clear selection: %s", exc)

    # DISABLE VIEWPORT SELECTION AND RIGHT-CLICK CONTEXT MENU.
    def _disable_viewport_interactions(self) -> None:
        # Look up the viewport window factory.
        try:
            from omni.kit.viewport.window import get_viewport_window_instances
        except ImportError as exc:
            LOGGER.warning("3D Draw: omni.kit.viewport.window unavailable: %s", exc)
        else:
            get_windows_any = cast(Any, get_viewport_window_instances)
            # Enumerate all open viewport windows.
            try:
                windows = list(get_windows_any())
            except Exception as exc:
                LOGGER.warning("3D Draw: could not enumerate viewport windows: %s", exc)
                windows = []

            # Viewport layers to hide while drawing.
            targets = [
                ("Selection", "manipulator"),
                ("ObjectClick", "manipulator"),
                ("ContextMenu", "manipulator"),
            ]
            # Hide each target layer per window and remember its prior visibility.
            for window in windows:
                find_layer = getattr(window, "_find_viewport_layer", None)
                if find_layer is None:
                    continue
                for layer_name, category in targets:
                    try:
                        layer = find_layer(layer_name, category)
                    except Exception:
                        layer = None
                    if layer is None:
                        continue
                    try:
                        prev_visible = bool(layer.visible)
                        layer.visible = False
                        self._hidden_layers.append((layer, prev_visible))
                    except Exception as exc:
                        LOGGER.warning(
                            "3D Draw: could not hide %s layer: %s", layer_name, exc
                        )

        # Override the viewport context-menu setting and remember the prior value.
        try:
            settings = cast(Any, carb.settings.get_settings())
            self._prev_context_menu = settings.get(_VIEWPORT_CONTEXT_MENU_SETTING)
            settings.set_bool(_VIEWPORT_CONTEXT_MENU_SETTING, False)
        except Exception as exc:
            LOGGER.warning("3D Draw: could not disable viewport context menu: %s", exc)
            self._prev_context_menu = _UNSET

    # RESTORE VIEWPORT LAYERS AND CONTEXT-MENU SETTING.
    def _restore_viewport_interactions(self) -> None:
        # Re-show every layer we hid.
        for layer, prev_visible in self._hidden_layers:
            try:
                layer.visible = prev_visible
            except Exception:
                pass
        self._hidden_layers.clear()

        # Restore the previous context-menu setting (or remove our override).
        if self._prev_context_menu is not _UNSET:
            try:
                settings = cast(Any, carb.settings.get_settings())
                if self._prev_context_menu is None:
                    # Drop the override so the kit default applies.
                    try:
                        settings.destroy_item(_VIEWPORT_CONTEXT_MENU_SETTING)
                    except Exception:
                        settings.set_bool(_VIEWPORT_CONTEXT_MENU_SETTING, True)
                else:
                    settings.set_bool(
                        _VIEWPORT_CONTEXT_MENU_SETTING, bool(self._prev_context_menu)
                    )
            except Exception as exc:
                LOGGER.warning("3D Draw: could not restore context menu setting: %s", exc)
        self._prev_context_menu = _UNSET

    # SUBSCRIBE TO KEYBOARD EVENTS FOR ESC.
    def _subscribe_keyboard(self) -> None:
        if self._keyboard_sub is not None:
            return
        try:
            app_window: Any = omni.appwindow.get_default_app_window()  # type: ignore[reportUnknownMemberType]
            keyboard = app_window.get_keyboard()
            input_iface = cast(Any, carb.input.acquire_input_interface())
            self._keyboard_sub = input_iface.subscribe_to_keyboard_events(
                keyboard, self._on_keyboard_event
            )
        except Exception as exc:
            LOGGER.warning("3D Draw: could not subscribe to keyboard events: %s", exc)
            self._keyboard_sub = None

    # UNSUBSCRIBE THE KEYBOARD LISTENER.
    def _unsubscribe_keyboard(self) -> None:
        if self._keyboard_sub is None:
            return
        try:
            app_window: Any = omni.appwindow.get_default_app_window()  # type: ignore[reportUnknownMemberType]
            keyboard = app_window.get_keyboard()
            input_iface = cast(Any, carb.input.acquire_input_interface())
            input_iface.unsubscribe_to_keyboard_events(keyboard, self._keyboard_sub)
        except Exception:
            pass
        self._keyboard_sub = None

    # DEACTIVATE ON ESC; RETURN TRUE TO KEEP PROPAGATING THE EVENT.
    def _on_keyboard_event(self, event: Any) -> bool:
        try:
            event_type = event.type
            key = event.input
        except Exception:
            return True

        if event_type == carb.input.KeyboardEventType.KEY_PRESS and key == carb.input.KeyboardInput.ESCAPE:
            self.deactivate()
        return True

    # ----- Drawing (sphere emission while LMB held) -----

    # REGISTER A TF LISTENER TO CLEAN UP PARENT STROKES WHEN PROTOTYPE IS DELETED.
    def _register_proto_listener(self) -> None:
        try:
            from pxr import Tf, Usd

            self._notice_listener = cast(Any, Tf).Notice.Register(
                cast(Any, Usd).Notice.ObjectsChanged,
                self._on_objects_changed,
                None,
            )
        except Exception:
            self._notice_listener = None

    # HANDLE USD OBJECT-CHANGED NOTICES TO REMOVE ORPHANED STROKE PARENTS.
    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        # Pull the resynced paths from this notice.
        try:
            paths = [str(p) for p in notice.GetResyncedPaths()]
        except Exception:
            return
        # Find stroke parents whose ProtoSphere child was removed.
        targets: list[str] = []
        for p in paths:
            if p.endswith("/ProtoSphere") and p.startswith(_DRAW_ROOT + "/"):
                parent = p.rsplit("/", 1)[0]
                if parent != _DRAW_ROOT:
                    targets.append(parent)
        if not targets:
            return
        # Remove each orphaned stroke prim from the root layer.
        try:
            from pxr import Sdf, Usd

            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                for parent in targets:
                    prim = stage.GetPrimAtPath(parent)
                    if prim.IsValid() and not stage.GetPrimAtPath(parent + "/ProtoSphere").IsValid():
                        stage.RemovePrim(Sdf.Path(parent))
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to remove orphaned stroke: %s", exc)

    # CURSOR LMB-DOWN: START A NEW STROKE AND EMIT THE FIRST STAMP.
    def _on_draw_begin(self, pos: tuple[float, float, float]) -> None:
        self._draw_pos = pos
        self._stroke_samples = [pos]
        self._last_stamp_pos = None
        self._stamp_carry = 0.0
        self._drawing = True
        # Allocate a fresh PointInstancer path for this stroke.
        self._stroke_path = self._allocate_stroke_path()
        # Assign the new stroke to the active group (Default by default).
        try:
            from .layers import GroupRegistry
            registry = GroupRegistry.get()
            if registry is not None and self._stroke_path:
                registry.assign_new_stroke(self._stroke_path)
        except Exception:
            pass
        # Force immediate processing on the first tick.
        self._accum = _EMIT_INTERVAL
        self._tick(0.0)
        self._start_emitting()

    # CURSOR MOVE: APPEND THE POSITION TO THE CURRENT STROKE'S SAMPLE BUFFER.
    def _on_draw_change(self, pos: tuple[float, float, float]) -> None:
        self._draw_pos = pos
        # Capture every cursor update so fast motion is preserved between ticks.
        if not self._stroke_samples or self._stroke_samples[-1] != pos:
            self._stroke_samples.append(pos)

    # CURSOR LMB-UP: FLUSH REMAINING SAMPLES AND RESET STROKE STATE.
    def _on_draw_end(self) -> None:
        # Flush remaining samples before stopping.
        if self._drawing:
            self._tick(_EMIT_INTERVAL)
        self._drawing = False
        self._stroke_samples = []
        self._last_stamp_pos = None
        self._stamp_carry = 0.0
        self._stroke_path = None
        self._stop_emitting()

    # PICK A UNIQUE PATH UNDER _DRAW_ROOT FOR THE NEW STROKE'S POINTINSTANCER.
    def _allocate_stroke_path(self) -> str:
        try:
            from pxr import Sdf, Usd, UsdGeom

            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return f"{_DRAW_ROOT}/Stroke"
            # Ensure the draw root Xform exists in the root layer.
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if not stage.GetPrimAtPath(_DRAW_ROOT).IsValid():
                    cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(_DRAW_ROOT))
            # Ask omni.usd for the next free `Stroke`, `Stroke_01`, ... path.
            base = f"{_DRAW_ROOT}/Stroke"
            return cast(Any, omni.usd.get_stage_next_free_path)(stage, base, False)
        except Exception:
            return f"{_DRAW_ROOT}/Stroke"

    # SUBSCRIBE TO PER-FRAME UPDATES SO _TICK RUNS WHILE DRAWING.
    def _start_emitting(self) -> None:
        if self._update_sub is not None:
            return
        try:
            app = cast(Any, omni.kit.app.get_app())
            stream = app.get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                self._on_update, name="varun.launcher.ui.three_d_draw.emit"
            )
        except Exception:
            self._update_sub = None

    # UNSUBSCRIBE THE PER-FRAME UPDATE.
    def _stop_emitting(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None

    # PER-FRAME CALLBACK: FORWARD DT INTO THE STAMP SCHEDULER.
    def _on_update(self, e: Any) -> None:
        if not self._drawing:
            return
        try:
            dt = float(e.payload.get("dt", 1.0 / 60.0))
        except Exception:
            dt = 1.0 / 60.0
        self._tick(dt)

    # SCHEDULE AND EMIT STAMPS FOR ACCUMULATED STROKE SAMPLES.
    def _tick(self, dt: float) -> None:
        # Throttle to _EMIT_INTERVAL.
        self._accum += dt
        if self._accum < _EMIT_INTERVAL:
            return
        self._accum = 0.0
        scene = Cursor3D.get_scene()
        if scene is None:
            return

        # Build this tick's polyline from the last stamp plus new samples.
        samples = self._stroke_samples
        if not samples:
            return
        polyline: list[tuple[float, float, float]] = []
        if self._last_stamp_pos is not None:
            polyline.append(self._last_stamp_pos)
        polyline.extend(samples)
        # Carry only the latest sample into the next tick.
        self._stroke_samples = [samples[-1]]
        if len(polyline) < 2:
            # First tick: emit a single stamp at the starting point.
            stamps = [polyline[0]]
            self._last_stamp_pos = polyline[0]
            self._stamp_carry = 0.0
        else:
            # Resample the polyline into uniformly spaced stamps.
            stamps, self._last_stamp_pos, self._stamp_carry = _resample_polyline(
                polyline, _STAMP_SPACING, self._stamp_carry,
                self._last_stamp_pos is None,
            )

        if not stamps:
            return
        # Expand each stamp into one or more world-space sphere positions on the grid.
        positions: list[tuple[float, float, float]] = []
        try:
            for c in stamps:
                positions.extend(scene.grid_world_positions_at(c))
        except Exception:
            return
        if positions:
            self._emit_spheres(positions)

    # APPEND POSITIONS TO THE CURRENT STROKE'S POINTINSTANCER.
    def _emit_spheres(self, positions: list[tuple[float, float, float]]) -> None:
        if not positions or self._stroke_path is None:
            return
        try:
            from pxr import Gf, Sdf, Usd, UsdGeom, Vt

            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return

            # Author into the root layer so strokes can be deleted from Stage/Layers.
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                UG = cast(Any, UsdGeom)
                # Ensure the draw root Xform exists.
                if not stage.GetPrimAtPath(_DRAW_ROOT).IsValid():
                    UG.Xform.Define(stage, Sdf.Path(_DRAW_ROOT))

                instancer_path = self._stroke_path
                instancer_prim = stage.GetPrimAtPath(instancer_path)
                # Lazily create the PointInstancer + sphere prototype on first emission.
                if not instancer_prim.IsValid():
                    instancer = UG.PointInstancer.Define(stage, Sdf.Path(instancer_path))
                    proto_path = f"{instancer_path}/ProtoSphere"
                    # Define the sphere prototype with radius and display color.
                    proto = UG.Sphere.Define(stage, Sdf.Path(proto_path))
                    proto.GetRadiusAttr().Set(_SPHERE_RADIUS)
                    proto.GetDisplayColorAttr().Set(
                        cast(Any, Vt).Vec3fArray([Gf.Vec3f(*_SPHERE_COLOR)])
                    )
                    # Flip prototype to a class prim so it isn't rendered/picked at origin.
                    try:
                        spec = stage.GetRootLayer().GetPrimAtPath(Sdf.Path(proto_path))
                        if spec is not None:
                            spec.specifier = cast(Any, Sdf).SpecifierClass
                    except Exception as exc:
                        LOGGER.warning("3D Draw: failed to mark prototype as class: %s", exc)
                    # Wire prototype + initialize empty position/index arrays.
                    instancer.CreatePrototypesRel().SetTargets([Sdf.Path(proto_path)])
                    instancer.CreateProtoIndicesAttr().Set(cast(Any, Vt).IntArray([]))
                    instancer.CreatePositionsAttr().Set(cast(Any, Vt).Vec3fArray([]))
                else:
                    instancer = UG.PointInstancer(instancer_prim)

                # Append new positions and prototype indices to the existing arrays.
                pos_attr = instancer.GetPositionsAttr()
                idx_attr = instancer.GetProtoIndicesAttr()
                cur_pos = list(pos_attr.Get() or [])
                cur_idx = list(idx_attr.Get() or [])
                for p in positions:
                    cur_pos.append(Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])))
                    cur_idx.append(0)
                pos_attr.Set(cast(Any, Vt).Vec3fArray(cur_pos))
                idx_attr.Set(cast(Any, Vt).IntArray(cur_idx))
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to emit spheres: %s", exc)
