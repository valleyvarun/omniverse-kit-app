"""Stage-owned communication data and selection, shared with the drawing tools."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import omni.kit.app
import omni.usd
from pxr import Tf, Usd, UsdGeom, Vt
from tool_box.tools.drawing.plane.selection import (
    DEFAULT_GROUND_PLANE_PATH,
    PLANE_ROOT,
    PlaneSelection,
)
from tool_box.tools.drawing.settings import DRAW_ROOT

GROUPS_KEY: str = "varun_launcher_groups"
LOCK_KEY: str = "lockedByLauncher"
ROOT_GROUP: str = "__root__"
STROKE_LAYER_PREFIX: str = "__stroke_layer__:"


@dataclass
class Group:
    name: str
    parent: str = ROOT_GROUP
    members: list[str] = field(default_factory=list)
    active: bool = False
    visible: bool = True
    locked: bool = False
    stroke_visible: bool = True
    stroke_locked: bool = False


@dataclass(frozen=True)
class Row:
    key: str
    name: str
    kind: str
    depth: int = 0


class CommunicationLayers:
    def __init__(self, changed: Callable[[], None]) -> None:
        self.context: Any = omni.usd.get_context()
        self.stage: Any = None
        self.groups: dict[str, Group] = {}
        self.planes: PlaneSelection = PlaneSelection.get()
        self._changed: Callable[[], None] = changed
        self._dirty: bool = True
        self._notice: Any = None
        self._strokes: set[str] = set()
        self._locked: set[str] = set()
        self._enforcing: bool = False
        self._stage_events: Any = self.context.get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event, name="clayers.stage"
        )
        self._updates: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="clayers.refresh")
        )
        self.planes.add_listener(self.refresh)

    # Defer UI changes until the next update, outside USD and widget callbacks.
    def refresh(self) -> None:
        self._dirty = True

    def _on_stage_event(self, event: Any) -> None:
        if event.type in (
            int(omni.usd.StageEventType.OPENED),
            int(omni.usd.StageEventType.CLOSED),
            int(omni.usd.StageEventType.SELECTION_CHANGED),
        ):
            self.refresh()

    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        path: Any
        if notice.GetResyncedPaths():
            self.refresh()
        for path in notice.GetChangedInfoOnlyPaths():
            if not path.IsPropertyPath() or path.name == "visibility":
                self.refresh()

    def _on_update(self, _event: Any) -> None:
        stage: Any = self.context.get_stage()
        if stage != self.stage:
            self._bind_stage(stage)
        if not self._dirty:
            return
        self._dirty = False
        if self.stage is not None:
            self._assign_new_strokes()
            self._sync_locks()
        self._changed()

    def _bind_stage(self, stage: Any) -> None:
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        self.stage = stage
        self.groups.clear()
        self._strokes.clear()
        self._locked.clear()
        if stage is not None:
            self._load_groups()
            self._strokes = set(self.paths(DRAW_ROOT))
            if not self.groups:
                self.create_group("Default Drawing", active=True)
            self._notice = cast(Any, Tf).Notice.Register(Usd.Notice.ObjectsChanged, self._on_objects_changed, stage)
        self.refresh()

    # Retain the old app's metadata format, including nested virtual groups.
    def _load_groups(self) -> None:
        data: Any = self.stage.GetRootLayer().customLayerData.get(GROUPS_KEY, {})
        if not isinstance(data, dict):
            return
        key: str
        value: Any
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            members: list[str] = []
            path: Any
            for path in value.get("members", []):
                members.append(str(path))
            self.groups[str(key)] = Group(
                name=str(value.get("name", "Group")),
                parent=str(value.get("parent", ROOT_GROUP)),
                members=members,
                active=bool(value.get("active", False)),
                visible=bool(value.get("visible", True)),
                locked=bool(value.get("locked", False)),
                stroke_visible=bool(value.get("stroke_visible", True)),
                stroke_locked=bool(value.get("stroke_locked", False)),
            )

    def _persist(self) -> None:
        if self.stage is None:
            return
        data: dict[str, Any] = dict(self.stage.GetRootLayer().customLayerData)
        snapshot: dict[str, dict[str, Any]] = {}
        key: str
        group: Group
        for key, group in self.groups.items():
            snapshot[key] = {
                "name": group.name,
                "parent": group.parent,
                "members": Vt.StringArray(group.members),
                "active": group.active,
                "visible": group.visible,
                "locked": group.locked,
                "stroke_visible": group.stroke_visible,
                "stroke_locked": group.stroke_locked,
            }
        data[GROUPS_KEY] = snapshot
        self.stage.GetRootLayer().customLayerData = data
        self.refresh()

    def paths(self, root: str) -> list[str]:
        parent: Any = self.stage.GetPrimAtPath(root) if self.stage is not None else None
        result: list[str] = []
        prim: Any
        if parent:
            for prim in parent.GetChildren():
                if not prim.GetName().startswith("_") and prim.GetTypeName() not in ("Material", "Shader"):
                    result.append(str(prim.GetPath()))
        return result

    @staticmethod
    def stroke_layer_key(group: str) -> str:
        return STROKE_LAYER_PREFIX + group

    def layer_group(self, key: str) -> str | None:
        if not key.startswith(STROKE_LAYER_PREFIX):
            return None
        group: str = key[len(STROKE_LAYER_PREFIX) :]
        return group if group == ROOT_GROUP or group in self.groups else None

    @staticmethod
    def is_stroke(path: str) -> bool:
        return path.startswith(DRAW_ROOT + "/")

    def stroke_paths(self, group: str) -> list[str]:
        candidates: list[str] = self.paths(DRAW_ROOT) if group == ROOT_GROUP else self.groups[group].members
        result: list[str] = []
        path: str
        for path in candidates:
            if (
                self.is_stroke(path)
                and self.stage is not None
                and self.stage.GetPrimAtPath(path)
                and (group != ROOT_GROUP or self.group_for(path) is None)
            ):
                result.append(path)
        return result

    def row_paths(self, key: str) -> list[str]:
        if key in self.groups:
            return self.group_paths(key)
        group: str | None = self.layer_group(key)
        return self.stroke_paths(group) if group is not None else [key]

    def _member_visible(self, path: str) -> bool:
        owner: str | None = self.group_for(path)
        if (
            owner is not None
            and owner in self.groups
            and self.is_stroke(path)
            and not self.groups[owner].stroke_visible
        ):
            return False
        seen: set[str] = set()
        while owner in self.groups and owner not in seen:
            assert owner is not None
            seen.add(owner)
            if not self.groups[owner].visible:
                return False
            owner = self.groups[owner].parent
        return True

    def group_for(self, path: str) -> str | None:
        key: str
        group: Group
        member: str
        for key, group in self.groups.items():
            for member in group.members:
                if path == member or path.startswith(member + "/"):
                    return key
        return None

    def group_paths(self, key: str, visited: set[str] | None = None) -> list[str]:
        seen: set[str] = set() if visited is None else visited
        if key in seen or key not in self.groups:
            return []
        seen.add(key)
        result: list[str] = list(self.groups[key].members)
        child: str
        group: Group
        for child, group in self.groups.items():
            if group.parent == key:
                result.extend(self.group_paths(child, seen))
        return result

    def group_locked(self, key: str | None) -> bool:
        seen: set[str] = set()
        while key in self.groups and key not in seen:
            assert key is not None
            seen.add(key)
            if self.groups[key].locked:
                return True
            key = self.groups[key].parent
        return False

    def locked(self, path: str) -> bool:
        layer_owner: str | None = self.layer_group(path)
        if layer_owner is not None:
            if layer_owner in self.groups:
                return self.group_locked(layer_owner) or self.groups[layer_owner].stroke_locked
            paths: list[str] = self.stroke_paths(ROOT_GROUP)
            member: str
            for member in paths:
                if not self.locked(member):
                    return False
            return bool(paths)
        owner: str | None = self.group_for(path)
        if self.group_locked(owner):
            return True
        if owner is not None and owner in self.groups and self.is_stroke(path) and self.groups[owner].stroke_locked:
            return True
        prim: Any = self.stage.GetPrimAtPath(path) if self.stage is not None and path.startswith("/") else None
        while prim and not prim.IsPseudoRoot():
            if prim.GetCustomDataByKey(LOCK_KEY):
                return True
            prim = prim.GetParent()
        return False

    def _sync_locks(self) -> None:
        locked: set[str] = set()
        prim: Any
        path: str
        for prim in self.stage.Traverse():
            path = str(prim.GetPath())
            if self.locked(path):
                locked.add(path)
        for path in self._locked - locked:
            self.context.set_pickable(path, True)
        for path in locked:
            self.context.set_pickable(path, False)
        self._locked = locked
        selection: list[str] = self.selected_paths()
        kept: list[str] = []
        for path in selection:
            if path not in locked:
                kept.append(path)
        if kept != selection and not self._enforcing:
            self._enforcing = True
            try:
                self.context.get_selection().set_selected_prim_paths(kept, False)
            finally:
                self._enforcing = False

    def selected_paths(self) -> list[str]:
        return list(self.context.get_selection().get_selected_prim_paths()) if self.stage is not None else []

    def select(self, paths: list[str]) -> None:
        if self.stage is not None:
            kept: list[str] = []
            path: str
            for path in paths:
                if path.startswith("/") and not self.locked(path):
                    kept.append(path)
            self.context.get_selection().set_selected_prim_paths(kept, False)

    def visible(self, path: str) -> bool:
        layer_owner: str | None = self.layer_group(path)
        if layer_owner is not None:
            if layer_owner in self.groups:
                return self.groups[layer_owner].stroke_visible
            member: str
            for member in self.stroke_paths(ROOT_GROUP):
                if self.visible(member):
                    return True
            return False
        prim: Any = self.stage.GetPrimAtPath(path) if self.stage is not None else None
        imageable: Any = cast(Any, UsdGeom).Imageable(prim) if prim else None
        return bool(imageable and imageable.ComputeVisibility() != UsdGeom.Tokens.invisible)

    def set_visible(self, key: str, visible: bool) -> None:
        if self.stage is None:
            return
        paths: list[str] = [key]
        owner: str | None
        if key in self.groups:
            self.groups[key].visible = visible
            paths = self.group_paths(key)
            self._persist()
        elif (owner := self.layer_group(key)) is not None:
            paths = self.stroke_paths(owner)
            if owner in self.groups:
                self.groups[owner].stroke_visible = visible
                self._persist()
        elif self.locked(key):
            return
        path: str
        for path in paths:
            prim: Any = self.stage.GetPrimAtPath(path)
            imageable: Any = cast(Any, UsdGeom).Imageable(prim) if prim else None
            if imageable:
                inherited: bool = visible and self._member_visible(path)
                imageable.GetVisibilityAttr().Set(UsdGeom.Tokens.inherited if inherited else UsdGeom.Tokens.invisible)
        self.refresh()

    def toggle_lock(self, key: str) -> None:
        owner: str | None
        if self.stage is None:
            return
        if key in self.groups:
            if not self.group_locked(self.groups[key].parent):
                self.groups[key].locked = not self.groups[key].locked
                self._persist()
        elif (owner := self.layer_group(key)) is not None:
            if owner in self.groups:
                if not self.group_locked(owner):
                    self.groups[owner].stroke_locked = not self.groups[owner].stroke_locked
                    self._persist()
            else:
                locked: bool = not self.locked(key)
                path: str
                for path in self.stroke_paths(owner):
                    self.stage.GetPrimAtPath(path).SetCustomDataByKey(LOCK_KEY, locked)
        elif not self.group_locked(self.group_for(key)):
            prim: Any = self.stage.GetPrimAtPath(key)
            if prim:
                prim.SetCustomDataByKey(LOCK_KEY, not bool(prim.GetCustomDataByKey(LOCK_KEY)))
        self.refresh()

    def activate(self, key: str) -> None:
        if self.stage is None:
            return
        if key in self.groups:
            parent: str = self.groups[key].parent
            group: Group
            for group in self.groups.values():
                if group.parent == parent:
                    group.active = group is self.groups[key]
            self._persist()
        elif self.layer_group(key) is None:
            self.planes.set_active_plane(key)

    def create_group(
        self, name: str, parent: str = ROOT_GROUP, members: list[str] | None = None, active: bool = False
    ) -> str:
        index: int = 0
        while f"g{index}" in self.groups:
            index += 1
        key: str = f"g{index}"
        paths: list[str] = []
        path: str
        for path in members or []:
            if path.startswith("/"):
                paths.append(path)
        group: Group
        for group in self.groups.values():
            for path in paths:
                if path in group.members:
                    group.members.remove(path)
        self.groups[key] = Group(name, parent, paths, active)
        self._persist()
        return key

    def ungroup(self, key: str) -> None:
        if key not in self.groups:
            return
        removed: Group = self.groups.pop(key)
        group: Group
        for group in self.groups.values():
            if group.parent == key:
                group.parent = removed.parent
        if removed.parent in self.groups:
            self.groups[removed.parent].members.extend(removed.members)
        self._persist()

    def _assign_new_strokes(self) -> None:
        current: set[str] = set(self.paths(DRAW_ROOT))
        added: set[str] = current - self._strokes
        self._strokes = current
        destination: str | None = None
        parent: str = ROOT_GROUP
        seen: set[str] = set()
        while parent not in seen:
            seen.add(parent)
            destination_next: str | None = None
            key: str
            group: Group
            for key, group in self.groups.items():
                if group.parent == parent and group.active:
                    destination_next = key
                    break
            if destination_next is None:
                break
            destination = parent = destination_next
        if added and destination is not None:
            path: str
            for path in sorted(added):
                if self.group_for(path) is None:
                    self.groups[destination].members.append(path)
                if not self._member_visible(path):
                    imageable: Any = cast(Any, UsdGeom).Imageable(self.stage.GetPrimAtPath(path))
                    if imageable:
                        imageable.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
            self._persist()

    # Stroke layers are virtual rows; their USD paths and group membership stay unchanged.
    def rows(self, expanded: set[str]) -> list[Row]:
        if self.stage is None:
            return []
        result: list[Row] = [Row(DEFAULT_GROUND_PLANE_PATH, "Ground Plane", "Plane")]
        visited: set[str] = set()

        def emit_strokes(group_key: str, depth: int) -> None:
            paths: list[str] = self.stroke_paths(group_key)
            if not paths:
                return
            layer: str = self.stroke_layer_key(group_key)
            result.append(Row(layer, "Strokes", "Stroke Layer", depth))
            if layer in expanded:
                path: str
                for path in paths:
                    result.append(Row(path, self.stage.GetPrimAtPath(path).GetName(), "Stroke", depth + 1))

        def emit(key: str, depth: int) -> None:
            if key in visited:
                return
            visited.add(key)
            group: Group = self.groups[key]
            result.append(Row(key, group.name, "Group", depth))
            if key not in expanded:
                return
            child: str
            value: Group
            for child, value in self.groups.items():
                if value.parent == key:
                    emit(child, depth + 1)
            path: str
            for path in group.members:
                prim: Any = self.stage.GetPrimAtPath(path)
                if prim and not self.is_stroke(path):
                    kind: str = "Plane" if path.startswith(PLANE_ROOT + "/") else str(prim.GetTypeName() or "Object")
                    result.append(Row(path, prim.GetName(), kind, depth + 1))
            emit_strokes(key, depth + 1)

        key: str
        group: Group
        for key, group in self.groups.items():
            if group.parent not in self.groups:
                emit(key, 0)
        path: str
        for path in self.paths(PLANE_ROOT):
            if self.group_for(path) is None:
                result.append(Row(path, path.rsplit("/", 1)[-1], "Plane"))
        emit_strokes(ROOT_GROUP, 0)
        return result

    def destroy(self) -> None:
        self.planes.remove_listener(self.refresh)
        self._updates = self._stage_events = None
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        path: str
        for path in self._locked:
            self.context.set_pickable(path, True)
        self.stage = None
        self.groups.clear()
