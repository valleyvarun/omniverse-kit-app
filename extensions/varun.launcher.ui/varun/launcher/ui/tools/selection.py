from __future__ import annotations

import logging
from typing import Any, cast

import omni.usd

# Re-export LockManager from its new central home so existing imports keep
# working. All lock/visibility/active-plane state lives in `layers.py`.
from .layers import LockManager

__all__ = [
    "LockManager",
    "SelectionDirectionFilter",
    "SelectionStyle",
    "SelectionSync",
    "ViewportXformFilter",
]


LOGGER = logging.getLogger(__name__)


# Mid-grey drag-select rectangle: visible against the Grey Studio background.
_OUTLINE_COLOR = (0.35, 0.35, 0.35, 1.0)
_INNER_COLOR = (0.35, 0.35, 0.35, 0.30)
_THICKNESS = 2.0


# Recolours the viewport drag-select rectangle.
class SelectionStyle:
    def __init__(self) -> None:
        self._patched = False
        self._original_init: Any = None

    # Apply style to future and existing SelectionManipulator instances.
    def apply(self) -> None:
        if self._patched:
            return
        try:
            from omni.kit.manipulator.selection import SelectionManipulator
        except ImportError as exc:
            LOGGER.warning("SelectionStyle: omni.kit.manipulator.selection unavailable: %s", exc)
            return

        self._patch_init(SelectionManipulator)
        self._restyle_existing()
        self._patched = True

    # Restore the original SelectionManipulator.__init__.
    def destroy(self) -> None:
        if not self._patched:
            return
        try:
            from omni.kit.manipulator.selection import SelectionManipulator
            if self._original_init is not None:
                cast(Any, SelectionManipulator).__init__ = self._original_init
        except Exception:
            pass
        self._original_init = None
        self._patched = False

    # Inject our style as the default for any newly-created manipulator.
    def _patch_init(self, manipulator_cls: Any) -> None:
        original_init = manipulator_cls.__init__
        self._original_init = original_init

        def patched(self_: Any, style: Any = None, *args: Any, **kwargs: Any) -> None:
            merged: Any = {
                "color": _OUTLINE_COLOR,
                "inner_color": _INNER_COLOR,
                "thickness": _THICKNESS,
            }
            if style:
                merged.update(style)
            original_init(self_, merged, *args, **kwargs)

        manipulator_cls.__init__ = patched

    # Recolour any SelectionManipulator already constructed by an open viewport.
    def _restyle_existing(self) -> None:
        try:
            from omni.kit.viewport.window import get_viewport_window_instances
        except ImportError:
            return

        for window in cast(Any, get_viewport_window_instances)():
            for manipulator in self._find_selection_manipulators(window):
                self._apply_to_instance(manipulator)

    # Walk a viewport window's scene layers looking for SelectionManipulator instances.
    def _find_selection_manipulators(self, window: Any) -> list[Any]:
        results: list[Any] = []
        try:
            from omni.kit.manipulator.selection import SelectionManipulator
        except ImportError:
            return results

        seen: set[int] = set()

        def walk(obj: Any, depth: int = 0) -> None:
            if obj is None or depth > 6 or id(obj) in seen:
                return
            seen.add(id(obj))
            if isinstance(obj, SelectionManipulator):
                results.append(obj)
                return
            for name in dir(obj):
                if not name.startswith("_") and name not in {"viewport_api", "scene_view"}:
                    continue
                if name in {"__class__", "__dict__", "__doc__", "__weakref__"}:
                    continue
                try:
                    walk(getattr(obj, name), depth + 1)
                except Exception:
                    continue

        walk(window)
        return results

    # Override the instance's mangled style attrs; new drags will pick up the colors.
    def _apply_to_instance(self, manipulator: Any) -> None:
        try:
            setattr(manipulator, "_SelectionManipulator__outline_color", _OUTLINE_COLOR)
            setattr(manipulator, "_SelectionManipulator__inner_color", _INNER_COLOR)
            setattr(manipulator, "_SelectionManipulator__thickness", _THICKNESS)
            # Force a rebuild so the new colors take effect on the next draw.
            invalidate = getattr(manipulator, "invalidate", None)
            if callable(invalidate):
                invalidate()
        except Exception as exc:
            LOGGER.warning("SelectionStyle: could not restyle manipulator: %s", exc)


# Râ†’L drag = enclose-only (custom selection). Lâ†’R = touch-select (kit default).
class SelectionDirectionFilter:
    def __init__(self) -> None:
        self._patched: bool = False
        self._original_handle: Any = None
        self._original_request: Any = None
        # Last (xmin,ymin,xmax,ymax,is_rtl,viewport_api,mode) seen during a drag.
        self._last_drag: Any = None

    # Hook the viewport selection manipulator.
    def apply(self) -> None:
        if self._patched:
            return
        try:
            from omni.kit.viewport.window.manipulator.selection import SelectionManipulatorItem
        except ImportError as exc:
            LOGGER.warning("SelectionDirectionFilter: viewport selection unavailable: %s", exc)
            return

        cls_any = cast(Any, SelectionManipulatorItem)
        outer = self

        # Capture drag direction from raw model points (ndc_rect is normalized).
        original_handle = cls_any._SelectionManipulatorItem__handle_selection
        self._original_handle = original_handle

        def patched_handle(self_: Any, model: Any, ndc_rect: Any, mode: Any, viewport_api: Any) -> Any:
            try:
                start = model.get_as_floats("ndc_start")
                end = model.get_as_floats("ndc_current") or start
                sx, ex = float(start[0]), float(end[0])
                sy, ey = float(start[1]), float(end[1])
                outer._last_drag = (
                    min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey),
                    ex > sx, viewport_api, mode,
                )
            except Exception:
                outer._last_drag = None
            return original_handle(self_, model, ndc_rect, mode, viewport_api)

        cls_any._SelectionManipulatorItem__handle_selection = patched_handle

        # On drag-end, if Râ†’L, do our own enclosure-based selection instead of the kit pick.
        original_request = cls_any._SelectionManipulatorItem__request_pick
        self._original_request = original_request

        def patched_request(self_: Any) -> Any:
            drag = outer._last_drag
            outer._last_drag = None
            if drag is None or not drag[4]:
                return original_request(self_)
            xmin, ymin, xmax, ymax, _is_rtl, viewport_api, mode = drag
            try:
                outer._select_enclosed((xmin, ymin, xmax, ymax), viewport_api, mode)
            except Exception as exc:
                LOGGER.warning("SelectionDirectionFilter: enclosure selection failed: %s", exc)
            # Clear kit's pending pick args so it doesn't fire its own pick after us.
            try:
                self_._SelectionManipulatorItem__selection_args = None
            except Exception:
                pass
            return None

        cls_any._SelectionManipulatorItem__request_pick = patched_request
        self._patched = True

    # Restore originals.
    def destroy(self) -> None:
        if not self._patched:
            return
        try:
            from omni.kit.viewport.window.manipulator.selection import SelectionManipulatorItem
            cls_any = cast(Any, SelectionManipulatorItem)
            if self._original_handle is not None:
                cls_any._SelectionManipulatorItem__handle_selection = self._original_handle
            if self._original_request is not None:
                cls_any._SelectionManipulatorItem__request_pick = self._original_request
        except Exception:
            pass
        self._original_handle = None
        self._original_request = None
        self._last_drag = None
        self._patched = False

    # Walk the stage and select prims whose world AABB is fully inside the NDC rect.
    def _select_enclosed(
        self,
        rect: tuple[float, float, float, float],
        viewport_api: Any,
        mode: Any,
    ) -> None:
        from pxr import Gf, Usd, UsdGeom

        usd_context = cast(Any, omni.usd.get_context())
        stage = usd_context.get_stage()
        if stage is None:
            return

        m = viewport_api.world_to_ndc
        usd_any = cast(Any, Usd)
        bbox_cache: Any = UsdGeom.BBoxCache(
            usd_any.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=True,
        )
        xmin, ymin, xmax, ymax = rect

        # World point through world_to_ndc with explicit perspective divide.
        def project(p: tuple[float, float, float]) -> tuple[float, float, float] | None:
            v: Any = Gf.Vec4d(float(p[0]), float(p[1]), float(p[2]), 1.0)
            r: Any = v * m
            w = float(r[3])
            if abs(w) < 1e-9:
                return None
            return (float(r[0]) / w, float(r[1]) / w, float(r[2]) / w)

        # True if all 8 AABB corners project inside the NDC rect (and in front).
        def fully_enclosed(prim: Any) -> bool:
            try:
                aabb = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if aabb.IsEmpty():
                    return False
                lo, hi = aabb.GetMin(), aabb.GetMax()
            except Exception:
                return False
            for cx in (lo[0], hi[0]):
                for cy in (lo[1], hi[1]):
                    for cz in (lo[2], hi[2]):
                        ndc = project((cx, cy, cz))
                        if ndc is None:
                            return False
                        nx, ny, nz = ndc
                        if not (xmin <= nx <= xmax and ymin <= ny <= ymax and -1.0 <= nz <= 1.0):
                            return False
            return True

        # Collect candidate prims: visible Imageable leaves under the default prim.
        hits: list[str] = []
        traverse_root = stage.GetPseudoRoot()
        for prim in traverse_root.GetAllChildren():
            self._collect_enclosed(prim, fully_enclosed, hits)

        selection = usd_context.get_selection()
        try:
            from omni.kit.manipulator.selection import SelectionMode
            mode_replace = mode == SelectionMode.REPLACE
            mode_remove = mode == SelectionMode.REMOVE
        except Exception:
            mode_replace, mode_remove = True, False

        if mode_replace:
            new_paths = hits
        else:
            current = list(selection.get_selected_prim_paths())
            current_set = set(current)
            if mode_remove:
                new_paths = [p for p in current if p not in set(hits)]
            else:
                new_paths = current + [p for p in hits if p not in current_set]

        selection.set_selected_prim_paths(new_paths, False)

    # Recursively gather enclosed prims; stop descending once a prim is fully enclosed.
    def _collect_enclosed(self, prim: Any, fully_enclosed: Any, out: list[str]) -> None:
        from pxr import UsdGeom

        if not prim or not prim.IsValid() or not prim.IsActive():
            return
        # Skip non-imageable / invisible prims.
        imageable: Any = UsdGeom.Imageable(prim)
        if imageable:
            try:
                vis: Any = imageable.ComputeVisibility()
                if vis == UsdGeom.Tokens.invisible:
                    return
            except Exception:
                pass

        if imageable and fully_enclosed(prim):
            out.append(str(prim.GetPath()))
            return

        for child in prim.GetChildren():
            self._collect_enclosed(child, fully_enclosed, out)


# Strips Xform prims from selections that originated in the 3D viewport.
# Stage-window picks are unaffected.
class ViewportXformFilter:
    def __init__(self) -> None:
        self._patched = False
        self._original_request: Any = None
        self._stage_sub: Any = None
        # True between viewport pick request and the resulting SELECTION_CHANGED.
        self._pick_pending = False
        self._enforcing = False

    # Hook viewport pick + selection events.
    def apply(self) -> None:
        if self._patched:
            return
        try:
            from omni.kit.viewport.window.manipulator.selection import SelectionManipulatorItem
        except ImportError as exc:
            LOGGER.warning("ViewportXformFilter: viewport selection unavailable: %s", exc)
            return

        cls_any = cast(Any, SelectionManipulatorItem)
        outer = self

        # Wrap __request_pick: every viewport pick flips a pending flag we
        # consume in the SELECTION_CHANGED handler.
        original_request = cls_any._SelectionManipulatorItem__request_pick
        self._original_request = original_request

        def patched_request(self_: Any) -> Any:
            outer._pick_pending = True
            return original_request(self_)

        cls_any._SelectionManipulatorItem__request_pick = patched_request

        try:
            ctx = cast(Any, omni.usd.get_context())
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.viewport_xform_filter"
            )
        except Exception:
            self._stage_sub = None

        self._patched = True

    # Restore originals.
    def destroy(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if not self._patched:
            return
        try:
            from omni.kit.viewport.window.manipulator.selection import SelectionManipulatorItem
            cls_any = cast(Any, SelectionManipulatorItem)
            if self._original_request is not None:
                cls_any._SelectionManipulatorItem__request_pick = self._original_request
        except Exception:
            pass
        self._original_request = None
        self._patched = False
        self._pick_pending = False

    # Filter Xforms out of the next SELECTION_CHANGED after a viewport pick.
    def _on_stage_event(self, event: Any) -> None:
        try:
            event_type = int(event.type)
            sel_changed = int(omni.usd.StageEventType.SELECTION_CHANGED)
        except Exception:
            return
        if event_type != sel_changed or not self._pick_pending or self._enforcing:
            return
        self._pick_pending = False
        try:
            ctx = cast(Any, omni.usd.get_context())
            stage = ctx.get_stage()
            if stage is None:
                return
            sel = ctx.get_selection()
            paths_obj: Any = sel.get_selected_prim_paths() or []
            paths: list[str] = [str(p) for p in cast("list[Any]", paths_obj)]
        except Exception:
            return
        kept: list[str] = []
        for path in paths:
            try:
                prim = stage.GetPrimAtPath(path)
                if prim and prim.IsValid() and prim.GetTypeName() == "Xform":
                    # Drag-select can promote a Mesh hit up to an ancestor
                    # Xform (e.g. /World). Don't drop the user's intended
                    # geometry: replace the Xform with its visible Mesh
                    # descendants so they end up selected instead.
                    descendants = self._collect_mesh_descendants(prim)
                    for desc in descendants:
                        if desc not in kept:
                            kept.append(desc)
                    continue
            except Exception:
                pass
            kept.append(path)
        if kept == paths:
            return
        self._enforcing = True
        try:
            sel.set_selected_prim_paths(kept, False)
        except Exception:
            pass
        finally:
            self._enforcing = False

    # Walk an Xform and collect paths of visible Mesh descendants.
    def _collect_mesh_descendants(self, prim: Any) -> list[str]:
        results: list[str] = []
        try:
            from pxr import UsdGeom
            for descendant in prim.GetAllChildren():
                self._gather_meshes(descendant, UsdGeom, results)
        except Exception:
            pass
        return results

    # Recursive helper: append visible Mesh paths under this prim.
    def _gather_meshes(self, prim: Any, UG: Any, out: list[str]) -> None:
        if not prim or not prim.IsValid() or not prim.IsActive():
            return
        try:
            imageable: Any = UG.Imageable(prim)
            if imageable:
                vis: Any = imageable.ComputeVisibility()
                if vis == UG.Tokens.invisible:
                    return
        except Exception:
            pass
        if prim.GetTypeName() == "Mesh":
            out.append(str(prim.GetPath()))
        for child in prim.GetChildren():
            self._gather_meshes(child, UG, out)


# Two-way bridge between the USD stage selection and the C_Layers panel.
# Subscribers (e.g. the C_Layers panel) get notified whenever the stage
# selection changes; they can also push their own selection back to the
# stage via push(). A re-entrancy guard prevents feedback loops.
class SelectionSync:
    _instance: "SelectionSync | None" = None

    def __init__(self) -> None:
        self._stage_sub: Any = None
        self._listeners: list[Any] = []
        self._pushing = False

    @classmethod
    def get(cls) -> "SelectionSync | None":
        return cls._instance

    def apply(self) -> None:
        SelectionSync._instance = self
        try:
            ctx = cast(Any, omni.usd.get_context())
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.selection_sync"
            )
        except Exception:
            self._stage_sub = None

    def destroy(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if SelectionSync._instance is self:
            SelectionSync._instance = None

    # CURRENT STAGE SELECTION AS A LIST OF USD PATHS.
    def current_paths(self) -> list[str]:
        try:
            sel = cast(Any, omni.usd.get_context()).get_selection()
            paths_obj: Any = sel.get_selected_prim_paths() or []
            return [str(p) for p in cast("list[Any]", paths_obj)]
        except Exception:
            return []

    # PUSH A NEW STAGE SELECTION (USED BY THE C_LAYERS PANEL ON CLICK).
    # The push flag suppresses the feedback notify so the caller doesn't
    # get echoed its own selection back.
    def push(self, paths: list[str]) -> None:
        try:
            sel = cast(Any, omni.usd.get_context()).get_selection()
        except Exception:
            return
        self._pushing = True
        try:
            sel.set_selected_prim_paths(list(paths), False)
        except Exception:
            pass
        finally:
            self._pushing = False

    def add_listener(self, callback: Any) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _on_stage_event(self, event: Any) -> None:
        try:
            if int(event.type) != int(omni.usd.StageEventType.SELECTION_CHANGED):
                return
        except Exception:
            return
        if self._pushing:
            return
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass
