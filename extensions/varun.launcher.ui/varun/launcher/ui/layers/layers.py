from __future__ import annotations

import logging
from typing import Any, cast

import omni.kit.commands
import omni.usd

from ..active_context import (
    add_active_context_listener,
    get_active_stage,
    get_active_usd_context,
    remove_active_context_listener,
)


LOGGER = logging.getLogger(__name__)


# Custom-data key written on prims the user has locked.
_LOCK_KEY = "lockedByLauncher"

# Root-layer customData key tracking which drawing plane is active.
_ACTIVE_PLANE_KEY = "varun_launcher_active_drawing_plane"

# Sentinel "path" representing the viewport's default ground grid.
DEFAULT_GROUND_PLANE_PATH = "__default_ground_plane__"

# USD roots used by the launcher's drawing tools.
DRAWING_PLANES_ROOT = "/World/DrawingPlanes"
STROKES_ROOT = "/World/ThreeDDraw"

# Group anchor constants. A group's "parent" field is either another group's
# id or one of these anchors describing where it lives in the CLayers tree.
GROUP_ANCHOR_ROOT = "__root__"
GROUP_ANCHOR_THREEDDRAW = "__threeddraw__"

# Root-layer customLayerData key holding the serialised group dict.
_GROUPS_KEY = "varun_launcher_groups"

# Default group name created inside the ThreeDDraw Xform on first run.
DEFAULT_STROKE_GROUP_NAME = "Default Drawing"

# Commands blocked for locked prims.
_DELETE_COMMANDS = {"DeletePrims", "DeletePrimsCommand"}


# CENTRAL OWNER OF LOCK STATE; ENFORCES IT ACROSS THE APP.
class LockManager:
    # Singleton handle for the column UI.
    _instance: "LockManager | None" = None

    def __init__(self) -> None:
        self._stage_sub: Any = None
        self._orig_execute: Any = None
        # Cache of effective-locked prim paths.
        self._locked_paths: set[str] = set()
        # Re-entrancy guard for the selection-changed handler.
        self._enforcing = False
        # Listeners notified whenever lock state changes.
        self._listeners: list[Any] = []

    @classmethod
    def get(cls) -> "LockManager | None":
        return cls._instance

    # WIRE ENFORCEMENT.
    def apply(self) -> None:
        LockManager._instance = self
        self._subscribe_stage_events()
        self._install_command_guard()
        self._refresh_locked_paths()
        self._apply_pickable_for_all()
        add_active_context_listener(self._on_active_context_changed)

    # TEAR DOWN.
    def destroy(self) -> None:
        remove_active_context_listener(self._on_active_context_changed)
        self._uninstall_command_guard()
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if LockManager._instance is self:
            LockManager._instance = None

    # Re-subscribe to the new active context's stage events and refresh
    # the locked-paths cache from the new stage. Fires lock listeners so
    # the C_Layers panel rebuilds against the new stage.
    def _on_active_context_changed(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        self._subscribe_stage_events()
        self._refresh_locked_paths()
        self._apply_pickable_for_all()
        self._notify_listeners()

    # ----- public API -----

    # TRUE IF THE PRIM ITSELF HAS THE LOCK CUSTOMDATA AUTHORED.
    def is_locked_self(self, prim: Any) -> bool:
        try:
            data = cast("dict[str, Any]", prim.GetCustomData() or {})
            return bool(data.get(_LOCK_KEY, False))
        except Exception:
            return False

    # TRUE IF THE PRIM IS EFFECTIVELY LOCKED (ITSELF OR ANY ANCESTOR LOCKED).
    def is_locked(self, prim: Any) -> bool:
        try:
            current = prim
            while current and current.IsValid():
                if self.is_locked_self(current):
                    return True
                parent = current.GetParent()
                if parent is None or not parent.IsValid() or parent == current:
                    return False
                if str(parent.GetPath()) in ("/", ""):
                    return False
                current = parent
        except Exception:
            return False
        return False

    # TOGGLE A PRIM'S OWN LOCK STATE; DESCENDANTS INHERIT VIA is_locked().
    def set_locked(self, prim: Any, locked: bool) -> None:
        try:
            if locked:
                prim.SetCustomDataByKey(_LOCK_KEY, True)
            else:
                prim.ClearCustomDataByKey(_LOCK_KEY)
        except Exception:
            return
        # Diff the cache and update viewport pickability for every change.
        previous = set(self._locked_paths)
        self._refresh_locked_paths()
        added = self._locked_paths - previous
        removed = previous - self._locked_paths
        for path in added:
            self._set_pickable(path, False)
        for path in removed:
            self._set_pickable(path, True)
        # Pickability is inherited in Hydra; re-assert unpickable on every
        # locked path so own-locked descendants stay unpickable when their
        # ancestor is unlocked.
        for path in self._locked_paths:
            self._set_pickable(path, False)
        if self._locked_paths:
            self._enforce_selection()
        self._notify_listeners()

    # REGISTER A CALLBACK FIRED WHENEVER LOCK STATE CHANGES.
    def add_listener(self, callback: Any) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    # UNREGISTER A PREVIOUSLY ADDED LISTENER.
    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    # INVOKE EVERY REGISTERED LISTENER.
    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # ----- internals -----

    # SUBSCRIBE TO STAGE EVENTS.
    def _subscribe_stage_events(self) -> None:
        try:
            ctx = get_active_usd_context()
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.lock_manager"
            )
        except Exception:
            self._stage_sub = None

    # REFRESH ON STAGE OPEN, FILTER ON SELECTION CHANGE.
    def _on_stage_event(self, event: Any) -> None:
        try:
            event_type = int(event.type)
            opened = int(omni.usd.StageEventType.OPENED)
            sel_changed = int(omni.usd.StageEventType.SELECTION_CHANGED)
        except Exception:
            return
        if event_type == opened:
            self._refresh_locked_paths()
            self._apply_pickable_for_all()
            self._notify_listeners()
        elif event_type == sel_changed:
            self._enforce_selection()

    # REBUILD THE EFFECTIVE-LOCKED-PATHS CACHE FROM THE STAGE.
    def _refresh_locked_paths(self) -> None:
        self._locked_paths.clear()
        try:
            from pxr import Usd
            stage = get_active_stage()
            if stage is None:
                return
            for prim in stage.Traverse():
                if self.is_locked_self(prim):
                    for descendant in cast(Any, Usd).PrimRange(prim):
                        self._locked_paths.add(str(descendant.GetPath()))
        except Exception:
            pass

    # MARK EVERY CACHED LOCKED PATH AS UNPICKABLE (E.G. ON STAGE OPEN).
    def _apply_pickable_for_all(self) -> None:
        for path in self._locked_paths:
            self._set_pickable(path, False)

    # WRAP UsdContext.set_pickable SO A MISSING API NEVER BREAKS LOCK TOGGLING.
    def _set_pickable(self, path: str, pickable: bool) -> None:
        try:
            ctx = get_active_usd_context()
            ctx.set_pickable(path, pickable)
        except Exception:
            pass

    # STRIP LOCKED PRIMS FROM THE CURRENT SELECTION.
    def _enforce_selection(self) -> None:
        if self._enforcing or not self._locked_paths:
            return
        try:
            sel = get_active_usd_context().get_selection()
            paths_obj: Any = sel.get_selected_prim_paths() or []
            paths: list[str] = [str(p) for p in cast("list[Any]", paths_obj)]
        except Exception:
            return
        kept: list[str] = [p for p in paths if p not in self._locked_paths]
        if len(kept) == len(paths):
            return
        self._enforcing = True
        try:
            sel.set_selected_prim_paths(kept, False)
        except Exception:
            pass
        finally:
            self._enforcing = False

    # WRAP omni.kit.commands.execute TO BLOCK DELETES ON LOCKED PRIMS.
    def _install_command_guard(self) -> None:
        if self._orig_execute is not None:
            return
        self._orig_execute = cast(Any, omni.kit.commands).execute
        outer = self

        def _guarded_execute(name: str, **kwargs: Any) -> Any:
            try:
                if name in _DELETE_COMMANDS:
                    raw: Any = kwargs.get("paths") or []
                    paths_list = list(raw)
                    kept = [p for p in paths_list if str(p) not in outer._locked_paths]
                    if not kept:
                        return (False, None)
                    kwargs["paths"] = kept
            except Exception:
                pass
            return outer._orig_execute(name, **kwargs)

        cast(Any, omni.kit.commands).execute = _guarded_execute

    # RESTORE omni.kit.commands.execute.
    def _uninstall_command_guard(self) -> None:
        if self._orig_execute is None:
            return
        try:
            cast(Any, omni.kit.commands).execute = self._orig_execute
        except Exception:
            pass
        self._orig_execute = None


# CENTRAL OWNER OF VISIBILITY STATE. WRAPS USDGEOM.IMAGEABLE VISIBILITY SO
# CHANGES MADE FROM STAGE'S EYE COLUMN, FROM C_LAYERS, OR FROM ANYWHERE ELSE
# ALL FLOW THROUGH (AND NOTIFY) THE SAME LISTENERS.
class VisibilityManager:
    _instance: "VisibilityManager | None" = None

    def __init__(self) -> None:
        self._stage_sub: Any = None
        self._notice_listener: Any = None
        self._listeners: list[Any] = []

    @classmethod
    def get(cls) -> "VisibilityManager | None":
        return cls._instance

    # WIRE LISTENERS.
    def apply(self) -> None:
        VisibilityManager._instance = self
        self._subscribe_stage_events()
        self._register_notice_listener()
        add_active_context_listener(self._on_active_context_changed)

    # TEAR DOWN.
    def destroy(self) -> None:
        remove_active_context_listener(self._on_active_context_changed)
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if self._notice_listener is not None:
            try:
                self._notice_listener.Revoke()
            except Exception:
                pass
            self._notice_listener = None
        if VisibilityManager._instance is self:
            VisibilityManager._instance = None

    # Re-subscribe to the new active context's stage events and fire
    # listeners so visibility-dependent panels rebuild.
    def _on_active_context_changed(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        self._subscribe_stage_events()
        self._notify_listeners()

    # TRUE IF THE PRIM'S OWN VISIBILITY ATTR IS NOT INVISIBLE.
    def is_visible_self(self, prim: Any) -> bool:
        try:
            from pxr import UsdGeom
            imageable: Any = cast(Any, UsdGeom).Imageable(prim)
            if not imageable:
                return True
            attr: Any = imageable.GetVisibilityAttr()
            if attr and attr.HasAuthoredValue():
                return str(attr.Get()) != str(cast(Any, UsdGeom).Tokens.invisible)
        except Exception:
            pass
        return True

    # TRUE IF THE PRIM IS EFFECTIVELY VISIBLE (ITSELF AND ALL ANCESTORS).
    def is_visible(self, prim: Any) -> bool:
        try:
            from pxr import UsdGeom
            imageable: Any = cast(Any, UsdGeom).Imageable(prim)
            if not imageable:
                return True
            vis = imageable.ComputeVisibility()
            return str(vis) != str(cast(Any, UsdGeom).Tokens.invisible)
        except Exception:
            return True

    # SET THIS PRIM'S OWN VISIBILITY (DESCENDANTS INHERIT).
    def set_visible(self, prim: Any, visible: bool) -> None:
        try:
            from pxr import UsdGeom
            imageable: Any = cast(Any, UsdGeom).Imageable(prim)
            if not imageable:
                return
            if visible:
                imageable.MakeVisible()
            else:
                imageable.MakeInvisible()
        except Exception:
            return
        self._notify_listeners()

    # REGISTER A CALLBACK FIRED WHENEVER VISIBILITY CHANGES (FROM ANY SOURCE).
    def add_listener(self, callback: Any) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # REFIRE LISTENERS WHEN A NEW STAGE OPENS.
    def _subscribe_stage_events(self) -> None:
        try:
            ctx = get_active_usd_context()
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.visibility_manager"
            )
        except Exception:
            self._stage_sub = None

    def _on_stage_event(self, event: Any) -> None:
        try:
            if int(event.type) == int(omni.usd.StageEventType.OPENED):
                self._notify_listeners()
        except Exception:
            pass

    # SUBSCRIBE TO USD ObjectsChanged AND FIRE LISTENERS WHEN ANY VISIBILITY
    # ATTRIBUTE CHANGES (E.G. STAGE'S EYE COLUMN TOGGLES).
    def _register_notice_listener(self) -> None:
        try:
            from pxr import Tf, Usd
            self._notice_listener = cast(Any, Tf).Notice.Register(
                cast(Any, Usd).Notice.ObjectsChanged,
                self._on_objects_changed,
                None,
            )
        except Exception:
            self._notice_listener = None

    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        try:
            changed = list(notice.GetChangedInfoOnlyPaths()) + list(notice.GetResyncedPaths())
        except Exception:
            return
        for path in changed:
            try:
                if str(path).endswith(".visibility"):
                    self._notify_listeners()
                    return
            except Exception:
                continue


# CENTRAL REGISTRY OF DRAWING PLANES, STROKES, AND THE ACTIVE DRAWING PLANE.
class LayerRegistry:
    _instance: "LayerRegistry | None" = None

    def __init__(self) -> None:
        self._stage_sub: Any = None
        self._notice_listener: Any = None
        self._listeners: list[Any] = []

    @classmethod
    def get(cls) -> "LayerRegistry | None":
        return cls._instance

    def apply(self) -> None:
        LayerRegistry._instance = self
        self._subscribe_stage_events()
        self._register_notice_listener()
        add_active_context_listener(self._on_active_context_changed)

    def destroy(self) -> None:
        remove_active_context_listener(self._on_active_context_changed)
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if self._notice_listener is not None:
            try:
                self._notice_listener.Revoke()
            except Exception:
                pass
            self._notice_listener = None
        if LayerRegistry._instance is self:
            LayerRegistry._instance = None

    # Re-subscribe to the new active context's stage events and fire
    # listeners. `_children_of` reads stage at call time so no cache to
    # rebuild here -- just nudge consumers to re-pull.
    def _on_active_context_changed(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        self._subscribe_stage_events()
        self._notify_listeners()

    # PATHS OF EVERY DRAWING PLANE PRIM CURRENTLY IN THE STAGE.
    def drawing_planes(self) -> list[str]:
        return self._children_of(DRAWING_PLANES_ROOT)

    # PATHS OF EVERY STROKE PRIM CURRENTLY IN THE STAGE.
    # Underscore-prefixed children (e.g. `_FlatPointsMat`, `_LiveStroke`,
    # `_LiveStrokeCurves`) are internal helpers (shared materials, in-progress
    # live-stroke temp prims) and are excluded from the layers panel.
    def strokes(self) -> list[str]:
        return [p for p in self._children_of(STROKES_ROOT)
                if not p.rsplit("/", 1)[-1].startswith("_")]

    # PATH (OR SENTINEL) OF THE CURRENTLY ACTIVE DRAWING PLANE.
    def active_plane(self) -> str:
        try:
            stage = get_active_stage()
            if stage is None:
                return DEFAULT_GROUND_PLANE_PATH
            data = cast("dict[str, Any]", stage.GetRootLayer().customLayerData or {})
            value = data.get(_ACTIVE_PLANE_KEY)
            if isinstance(value, str) and value:
                # If the recorded path no longer exists, fall back to default.
                if value == DEFAULT_GROUND_PLANE_PATH:
                    return value
                if stage.GetPrimAtPath(value).IsValid():
                    return value
        except Exception:
            pass
        return DEFAULT_GROUND_PLANE_PATH

    # SET THE ACTIVE DRAWING PLANE (PASS DEFAULT_GROUND_PLANE_PATH FOR GRID).
    def set_active_plane(self, path: str) -> None:
        try:
            stage = get_active_stage()
            if stage is None:
                return
            root_layer = stage.GetRootLayer()
            data = dict(root_layer.customLayerData or {})
            data[_ACTIVE_PLANE_KEY] = path
            root_layer.customLayerData = data
        except Exception:
            return
        self._notify_listeners()

    # REGISTER A CALLBACK FIRED WHEN PLANES, STROKES, OR ACTIVE-PLANE CHANGE.
    def add_listener(self, callback: Any) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # ----- internals -----

    def _children_of(self, root_path: str) -> list[str]:
        try:
            stage = get_active_stage()
            if stage is None:
                return []
            root = stage.GetPrimAtPath(root_path)
            if not root or not root.IsValid():
                return []
            return [str(p.GetPath()) for p in root.GetChildren() if p.IsValid()]
        except Exception:
            return []

    def _subscribe_stage_events(self) -> None:
        try:
            ctx = get_active_usd_context()
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.layer_registry"
            )
        except Exception:
            self._stage_sub = None

    def _on_stage_event(self, event: Any) -> None:
        try:
            if int(event.type) == int(omni.usd.StageEventType.OPENED):
                self._notify_listeners()
        except Exception:
            pass

    def _register_notice_listener(self) -> None:
        try:
            from pxr import Tf, Usd
            self._notice_listener = cast(Any, Tf).Notice.Register(
                cast(Any, Usd).Notice.ObjectsChanged,
                self._on_objects_changed,
                None,
            )
        except Exception:
            self._notice_listener = None

    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        # Refresh whenever something is added/removed under either root.
        try:
            paths = [str(p) for p in notice.GetResyncedPaths()]
        except Exception:
            return
        for path in paths:
            if path.startswith(DRAWING_PLANES_ROOT) or path.startswith(STROKES_ROOT):
                self._notify_listeners()
                return


# CENTRAL OWNER OF GROUPS. GROUPS ARE A VIRTUAL CLAYERS-ONLY CONCEPT
# (USD PATHS ARE NEVER REPARENTED). EACH GROUP HAS A PARENT (ANOTHER GROUP
# ID OR AN ANCHOR), A NAME, MEMBERS (USD PATHS), AND ITS OWN ACTIVE FLAG
# (UNIQUE PER PARENT). CHILD GROUPS ARE SIBLINGS UNDER THE SAME PARENT.
# PERSISTED IN ROOT-LAYER customLayerData[_GROUPS_KEY].
class GroupRegistry:
    _instance: "GroupRegistry | None" = None

    def __init__(self) -> None:
        self._stage_sub: Any = None
        # In-memory copy: id -> {name, parent, members(list[str]), active(bool)}.
        self._groups: dict[str, dict[str, Any]] = {}
        self._listeners: list[Any] = []
        # Currently CLayers-selected group id (drives the Ungroup button).
        self._selected_group: str | None = None

    @classmethod
    def get(cls) -> "GroupRegistry | None":
        return cls._instance

    # WIRE STAGE EVENTS AND ENSURE THE DEFAULT STROKE GROUP EXISTS.
    def apply(self) -> None:
        GroupRegistry._instance = self
        self._subscribe_stage_events()
        self._load()
        self._ensure_default_group()
        add_active_context_listener(self._on_active_context_changed)

    # TEAR DOWN.
    def destroy(self) -> None:
        remove_active_context_listener(self._on_active_context_changed)
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        if GroupRegistry._instance is self:
            GroupRegistry._instance = None

    # Re-subscribe to the new active context's stage events, reload the
    # group dict from the new stage's customLayerData, ensure a default
    # group exists on it, and fire listeners.
    def _on_active_context_changed(self) -> None:
        if self._stage_sub is not None:
            try:
                self._stage_sub.unsubscribe()
            except Exception:
                pass
            self._stage_sub = None
        self._subscribe_stage_events()
        self._load()
        self._ensure_default_group()
        self._notify_listeners()

    # ----- public API -----

    # ALL GROUP IDS WHOSE parent EQUALS THE GIVEN ANCHOR OR GROUP ID.
    def child_groups_of(self, parent_id: str) -> list[str]:
        return [gid for gid, g in self._groups.items() if g.get("parent") == parent_id]

    # USD PATHS DIRECTLY INSIDE THE GIVEN GROUP.
    def members_of(self, group_id: str) -> list[str]:
        g = self._groups.get(group_id)
        if g is None:
            return []
        return list(g.get("members", []))

    # GROUP ID THIS USD PATH BELONGS TO, OR None.
    def group_of_path(self, path: str) -> str | None:
        for gid, g in self._groups.items():
            if path in g.get("members", []):
                return gid
        return None

    # PARENT (ANCHOR OR PARENT GROUP ID) FOR THE GIVEN GROUP, OR None.
    def parent_of(self, group_id: str) -> str | None:
        g = self._groups.get(group_id)
        return str(g.get("parent")) if g else None

    # ----- group visibility / lock (mirror prim semantics) -----

    # OWN VISIBILITY FLAG OF THIS GROUP (DEFAULT TRUE).
    def is_visible_self(self, group_id: str) -> bool:
        g = self._groups.get(group_id)
        return bool(g.get("visible", True)) if g else True

    # OWN LOCK FLAG OF THIS GROUP (DEFAULT FALSE).
    def is_locked_self(self, group_id: str) -> bool:
        g = self._groups.get(group_id)
        return bool(g.get("locked", False)) if g else False

    # SET THE GROUP'S VISIBILITY AND PROPAGATE TO ALL MEMBER PRIMS AND
    # NESTED CHILD GROUPS (SAME SEMANTICS AS A USD PARENT PRIM).
    def set_visible(self, group_id: str, visible: bool) -> None:
        g = self._groups.get(group_id)
        if g is None:
            return
        g["visible"] = bool(visible)
        self._apply_to_members(group_id, visible=visible)
        self._persist()
        self._notify_listeners()

    # SET THE GROUP'S LOCK AND PROPAGATE TO ALL MEMBER PRIMS AND CHILDREN.
    # WHEN LOCKING, FIRST SNAPSHOT EACH MEMBER'S OWN LOCK STATE SO THAT
    # UNLOCKING THE GROUP RESTORES INDIVIDUAL LOCKS INSTEAD OF CLEARING
    # THEM. THE SNAPSHOT IS PERSISTED ON THE GROUP DICT.
    def set_locked(self, group_id: str, locked: bool) -> None:
        g = self._groups.get(group_id)
        if g is None:
            return
        was_locked = bool(g.get("locked", False))
        if bool(locked) == was_locked:
            return
        if locked:
            # Capture per-prim own-lock state for every prim under this group
            # (and nested child groups) BEFORE we overwrite them.
            snapshot = self._capture_prim_lock_snapshot(group_id)
            g["lock_snapshot"] = snapshot
            g["locked"] = True
            self._apply_to_members(group_id, locked=True)
        else:
            g["locked"] = False
            snapshot = cast("dict[str, bool]", g.pop("lock_snapshot", {}) or {})
            # Restore each prim to its pre-lock state. Prims that were
            # individually locked before stay locked; prims that weren't get
            # cleared. Prims absent from the snapshot fall back to unlocked.
            self._restore_prim_lock_snapshot(group_id, snapshot)
        self._persist()
        self._notify_listeners()

    # GATHER ALL USD PATHS UNDER A GROUP (RECURSIVE) AND THEIR CURRENT
    # OWN-LOCK STATE FROM LockManager. KEYS ARE STRING PATHS.
    def _capture_prim_lock_snapshot(self, group_id: str) -> dict[str, bool]:
        snapshot: dict[str, bool] = {}
        lock_mgr = LockManager.get()
        try:
            stage = get_active_stage()
        except Exception:
            stage = None

        def _walk(gid: str) -> None:
            for path in self.members_of(gid):
                if stage is None or lock_mgr is None:
                    snapshot.setdefault(path, False)
                    continue
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    snapshot.setdefault(path, False)
                    continue
                snapshot[path] = bool(cast(Any, lock_mgr).is_locked_self(prim))
            for child_gid in self.child_groups_of(gid):
                _walk(child_gid)

        _walk(group_id)
        return snapshot

    # APPLY A SNAPSHOT BACK TO LockManager. ANY MEMBER NOT IN THE SNAPSHOT
    # IS CONSERVATIVELY UNLOCKED.
    def _restore_prim_lock_snapshot(
        self,
        group_id: str,
        snapshot: dict[str, bool],
    ) -> None:
        lock_mgr = LockManager.get()
        try:
            stage = get_active_stage()
        except Exception:
            stage = None

        def _walk(gid: str) -> None:
            for path in self.members_of(gid):
                if stage is None or lock_mgr is None:
                    continue
                prim = stage.GetPrimAtPath(path)
                if not prim or not prim.IsValid():
                    continue
                desired = bool(snapshot.get(path, False))
                cast(Any, lock_mgr).set_locked(prim, desired)
            for child_gid in self.child_groups_of(gid):
                _walk(child_gid)

        _walk(group_id)

    # WALK MEMBERS + NESTED GROUPS, FORWARDING TO THE PRIM-LEVEL MANAGERS.
    def _apply_to_members(
        self,
        group_id: str,
        visible: bool | None = None,
        locked: bool | None = None,
    ) -> None:
        try:
            stage = get_active_stage()
        except Exception:
            stage = None
        for path in self.members_of(group_id):
            if stage is None:
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            if visible is not None and VisibilityManager.get() is not None:
                cast(Any, VisibilityManager.get()).set_visible(prim, visible)
            if locked is not None and LockManager.get() is not None:
                cast(Any, LockManager.get()).set_locked(prim, locked)
        for child_gid in self.child_groups_of(group_id):
            self._apply_to_members(child_gid, visible=visible, locked=locked)

    # GROUP DISPLAY NAME (OR EMPTY STRING IF UNKNOWN).
    def get_name(self, group_id: str) -> str:
        g = self._groups.get(group_id)
        return str(g.get("name", "")) if g else ""

    # WHETHER THE GROUP IS THE ACTIVE ONE AMONG ITS SIBLINGS.
    def is_active(self, group_id: str) -> bool:
        g = self._groups.get(group_id)
        return bool(g.get("active", False)) if g else False

    # SET THIS GROUP AS THE GLOBALLY ACTIVE GROUP. AT MOST ONE GROUP IS
    # ACTIVE AT A TIME ACROSS THE ENTIRE TREE; ALL OTHERS ARE DEACTIVATED.
    def set_active(self, group_id: str) -> None:
        if group_id not in self._groups:
            return
        for gid, other in self._groups.items():
            other["active"] = (gid == group_id)
        self._persist()
        self._notify_listeners()

    # CREATE A NEW GROUP. RETURNS THE NEW ID. THE GROUP IS PLACED UNDER
    # parent_id (ANCHOR OR ANOTHER GROUP). MEMBERS ARE OPTIONALLY SEEDED.
    # IF make_active IS TRUE, THE NEW GROUP IS MADE THE ACTIVE SIBLING.
    def create_group(
        self,
        name: str,
        parent_id: str,
        member_paths: list[str] | None = None,
        make_active: bool = False,
    ) -> str:
        new_id = self._allocate_id()
        # If members come from elsewhere, detach them from their previous group.
        members: list[str] = []
        if member_paths:
            for p in member_paths:
                self._remove_from_any_group(p)
                if p not in members:
                    members.append(p)
        self._groups[new_id] = {
            "name": name,
            "parent": parent_id,
            "members": members,
            "active": False,
            "visible": True,
            "locked": False,
        }
        if make_active:
            self.set_active(new_id)
        else:
            self._persist()
            self._notify_listeners()
        return new_id

    # DELETE A GROUP. ITS MEMBERS BECOME UNGROUPED (PARENT-LESS UNDER THE
    # GROUP'S OWN PARENT ANCHOR). CHILD GROUPS ARE REPARENTED TO THIS
    # GROUP'S PARENT TOO. IF THE GROUP WAS ACTIVE, NO SIBLING IS PROMOTED.
    def ungroup(self, group_id: str) -> None:
        g = self._groups.pop(group_id, None)
        if g is None:
            return
        new_parent = g.get("parent")
        # Reparent any child groups to the deleted group's parent.
        for child in self._groups.values():
            if child.get("parent") == group_id:
                child["parent"] = new_parent
        # If the deleted group's parent is itself a real group, hand the
        # path-members up to it; otherwise (root/anchor) they become
        # ungrouped at top level.
        if new_parent in self._groups:
            parent_members = self._groups[new_parent].setdefault("members", [])
            for path in g.get("members", []):
                if path not in parent_members:
                    parent_members.append(path)
        if self._selected_group == group_id:
            self._selected_group = None
        self._persist()
        self._notify_listeners()

    # ADD A USD PATH TO A GROUP (REMOVING IT FROM ITS PREVIOUS GROUP IF ANY).
    def add_member(self, group_id: str, path: str) -> None:
        if group_id not in self._groups:
            return
        self._remove_from_any_group(path)
        members = self._groups[group_id].setdefault("members", [])
        if path not in members:
            members.append(path)
        self._persist()
        self._notify_listeners()

    # REMOVE A USD PATH FROM WHATEVER GROUP HOLDS IT (NO-OP IF UNGROUPED).
    def remove_path(self, path: str) -> None:
        if self._remove_from_any_group(path):
            self._persist()
            self._notify_listeners()

    # CLAYERS SELECTION (USED BY THE UNGROUP BUTTON).
    def selected_group(self) -> str | None:
        return self._selected_group

    def set_selected_group(self, group_id: str | None) -> None:
        self._selected_group = group_id
        self._notify_listeners()

    # FIND THE GLOBALLY ACTIVE GROUP (NEW STROKES ARE ADDED HERE).
    def active_stroke_destination(self) -> str | None:
        for gid, g in self._groups.items():
            if g.get("active"):
                return gid
        return None

    # CONVENIENCE: THREE_D_DRAW CALLS THIS RIGHT AFTER ALLOCATING A STROKE.
    def assign_new_stroke(self, stroke_path: str) -> None:
        gid = self.active_stroke_destination()
        if gid is not None:
            self.add_member(gid, stroke_path)

    # LISTENERS.
    def add_listener(self, callback: Any) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    # ----- internals -----

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    # Walk active children downward under a starting anchor/group id.
    def _deepest_active_under(self, parent_id: str) -> str | None:
        last: str | None = None
        cursor = parent_id
        while True:
            active_child: str | None = None
            for gid, g in self._groups.items():
                if g.get("parent") == cursor and g.get("active"):
                    active_child = gid
                    break
            if active_child is None:
                return last
            last = active_child
            cursor = active_child

    def _remove_from_any_group(self, path: str) -> bool:
        for g in self._groups.values():
            members = g.get("members", [])
            if path in members:
                members.remove(path)
                return True
        return False

    def _allocate_id(self) -> str:
        n = 0
        while f"g{n}" in self._groups:
            n += 1
        return f"g{n}"

    # Deserialise from root-layer customLayerData on apply()/stage open.
    def _load(self) -> None:
        self._groups.clear()
        try:
            stage = get_active_stage()
            if stage is None:
                return
            data = cast("dict[str, Any]", stage.GetRootLayer().customLayerData or {})
            raw = data.get(_GROUPS_KEY)
            if not isinstance(raw, dict):
                return
            for gid, g_any in cast("dict[str, Any]", raw).items():
                if not isinstance(g_any, dict):
                    continue
                g = cast("dict[str, Any]", g_any)
                snap_raw = g.get("lock_snapshot")
                lock_snapshot: dict[str, bool] = {}
                if isinstance(snap_raw, dict):
                    for k, v in cast("dict[str, Any]", snap_raw).items():
                        lock_snapshot[str(k)] = bool(v)
                self._groups[str(gid)] = {
                    "name": str(g.get("name", "Group")),
                    "parent": str(g.get("parent", GROUP_ANCHOR_ROOT)),
                    "members": [str(m) for m in cast("list[Any]", g.get("members") or [])],
                    "active": bool(g.get("active", False)),
                    "visible": bool(g.get("visible", True)),
                    "locked": bool(g.get("locked", False)),
                    "lock_snapshot": lock_snapshot,
                }
        except Exception:
            pass

    # Serialise back to root-layer customLayerData.
    def _persist(self) -> None:
        try:
            stage = get_active_stage()
            if stage is None:
                return
            root_layer = stage.GetRootLayer()
            data = dict(root_layer.customLayerData or {})
            # Snapshot to plain dicts/lists so the value is serialisable.
            snapshot: dict[str, dict[str, Any]] = {}
            for gid, g in self._groups.items():
                snapshot[gid] = {
                    "name": g.get("name", ""),
                    "parent": g.get("parent", GROUP_ANCHOR_ROOT),
                    "members": list(g.get("members", [])),
                    "active": bool(g.get("active", False)),
                    "visible": bool(g.get("visible", True)),
                    "locked": bool(g.get("locked", False)),
                    "lock_snapshot": dict(g.get("lock_snapshot", {}) or {}),
                }
            data[_GROUPS_KEY] = snapshot
            root_layer.customLayerData = data
        except Exception:
            pass

    # Make sure a Default Drawing group exists at the root and is active.
    def _ensure_default_group(self) -> None:
        # If any group is already active globally, leave the tree alone.
        if any(g.get("active") for g in self._groups.values()):
            return
        # Otherwise, find an existing root-level group named Default Drawing
        # (or any root group) and activate it; create one if none exist.
        root_groups = [
            gid for gid, g in self._groups.items()
            if g.get("parent") == GROUP_ANCHOR_ROOT
        ]
        if not root_groups:
            self.create_group(
                DEFAULT_STROKE_GROUP_NAME,
                GROUP_ANCHOR_ROOT,
                member_paths=None,
                make_active=True,
            )
            return
        self.set_active(root_groups[0])

    def _subscribe_stage_events(self) -> None:
        try:
            ctx = get_active_usd_context()
            stream = ctx.get_stage_event_stream()
            self._stage_sub = stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui.group_registry"
            )
        except Exception:
            self._stage_sub = None

    def _on_stage_event(self, event: Any) -> None:
        try:
            if int(event.type) == int(omni.usd.StageEventType.OPENED):
                self._load()
                self._ensure_default_group()
                self._notify_listeners()
        except Exception:
            pass
