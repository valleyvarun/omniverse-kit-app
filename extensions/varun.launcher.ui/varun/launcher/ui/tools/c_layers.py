from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

import carb.input
import omni.appwindow
import omni.kit.app
import omni.ui as ui
import omni.usd

from .layers import (
    DEFAULT_GROUND_PLANE_PATH,
    GROUP_ANCHOR_ROOT,
    GROUP_ANCHOR_THREEDDRAW,
    STROKES_ROOT,
    GroupRegistry,
    LayerRegistry,
    LockManager,
    VisibilityManager,
)
from .selection import SelectionSync


LOGGER = logging.getLogger(__name__)

# Icon directory.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "logos"
_ICON_LOCKED = str(_ICONS_DIR / "lock_locked.svg")
_ICON_UNLOCKED = str(_ICONS_DIR / "lock_unlocked.svg")
_ICON_EYE_ON = str(_ICONS_DIR / "eye_on.svg")
_ICON_EYE_OFF = str(_ICONS_DIR / "eye_off.svg")
_ICON_ACTIVE_ON = str(_ICONS_DIR / "active_on.svg")
_ICON_ACTIVE_OFF = str(_ICONS_DIR / "active_off.svg")
_ICON_PLUS = str(_ICONS_DIR / "plus.svg")
_ICON_MINUS = str(_ICONS_DIR / "minus.svg")
_ICON_GROUP = str(_ICONS_DIR / "group.svg")
_ICON_UNGROUP = str(_ICONS_DIR / "ungroup.svg")
_ICON_ADD_GROUP = str(_ICONS_DIR / "add_group.svg")

# Disclosure gutter width.
_DISCLOSURE_WIDTH = ui.Pixel(14)

# Column widths. EVERY cell (header and row) is rendered inside an
# HStack(width=<one of these>), so the columns ALWAYS line up regardless
# of what's drawn inside.
_NAME_WIDTH = ui.Pixel(140)
_TYPE_WIDTH = ui.Pixel(64)
_VIS_WIDTH = ui.Pixel(28)
_LOCK_WIDTH = ui.Pixel(28)
_ACTIVE_WIDTH = ui.Pixel(28)
_ROW_HEIGHT = ui.Pixel(22)
_BUTTON_BAR_HEIGHT = _ROW_HEIGHT
# Width of the omni.ui ScrollingFrame vertical scrollbar gutter; used by the
# header/footer gutter spacers when the list is overflowing.
_SCROLLBAR_GUTTER = ui.Pixel(12)

# Visual icon sizes (square; centered inside a row-height cell).
_ICON_VISUAL = ui.Pixel(13)
_DISCLOSURE_VISUAL = ui.Pixel(9)
_BUTTON_ICON_VISUAL = _ICON_VISUAL

# Per-level indent applied to nested rows.
_INDENT_PER_LEVEL = 14
# Vertical guide line drawn at the left edge of each indent step (like the
# indent guides in code editors).
_INDENT_GUIDE_STYLE = {"background_color": 0xFFFFFFFF}
_INDENT_GUIDE_WIDTH = ui.Pixel(1)

# Item kinds.
_KIND_PLANE = "Plane"
_KIND_STROKE = "Stroke"
_KIND_XFORM = "Xform"
_KIND_GROUP = "Group"

# Styles.
_HEADER_STYLE = {"background_color": 0xFF2A2A2A}
_BUTTON_BAR_STYLE = _HEADER_STYLE
_ROW_STYLE = {"background_color": 0x00000000}
# Maroon: highlights the currently-active drawing plane.
_ROW_ACTIVE_PLANE_STYLE = {"background_color": 0xFF3A2D4D}
# Dark green: highlights the currently-active group.
_ROW_ACTIVE_GROUP_STYLE = {"background_color": 0xFF1F5F1F}
_ROW_SELECTED_STYLE = {"background_color": 0xFF2A3A55}
# Blue rectangle drawn around a row when the user clicks its name.
_ROW_SELECTION_BORDER_STYLE: dict[str, Any] = {
    "background_color": 0x00000000,
    "border_color": 0xFF3399FF,
    "border_width": 1.5,
}
_LABEL_STYLE = {"color": 0xFFCCCCCC, "font_size": 13}
_HEADER_LABEL_STYLE = {"color": 0xFFAAAAAA, "font_size": 12}
_BUTTON_STYLE = {
    "background_color": 0xFF2E2E2E,
    "border_radius": 3,
    "padding": 4,
}


# DESCRIBES A SINGLE ROW IN THE C_LAYERS WINDOW.
class _Item:
    def __init__(
        self,
        kind: str,
        name: str,
        indent: int = 0,
        path: str = "",
        prim: Any | None = None,
        group_id: str | None = None,
    ) -> None:
        self.kind = kind
        self.name = name
        self.indent = indent
        self.path = path
        self.prim = prim
        self.group_id = group_id

    # KEY USED FOR EXPAND/COLLAPSE STATE (UNIQUE PER COLLAPSIBLE ROW).
    def expand_key(self) -> str:
        if self.group_id is not None:
            return f"group:{self.group_id}"
        return f"path:{self.path}"


# C_LAYERS WINDOW PANEL. RENDERS DRAWING PLANES, STROKES, AND VIRTUAL GROUPS
# WITH FOUR COLUMNS (TYPE, VISIBILITY, LOCK, ACTIVE) PLUS A BOTTOM BUTTON BAR
# WITH GROUP/UNGROUP ACTIONS. ALL STATE LIVES IN THE CENTRAL MANAGERS IN
# layers.py.
class ClayersPanel:
    def __init__(self) -> None:
        self._frame: ui.Frame | None = None
        self._list_frame: ui.Frame | None = None
        # ScrollingFrame around the list, used to detect when its vertical
        # scrollbar is visible so we can mirror that gutter on the header
        # and footer (keeping all three the same total width).
        self._scroll_frame: Any = None
        self._header_gutter: Any = None
        self._footer_gutter: Any = None
        # Cached listener callbacks so we can deregister them on destroy.
        self._lock_cb: Any = None
        self._vis_cb: Any = None
        self._registry_cb: Any = None
        self._group_cb: Any = None
        self._selection_cb: Any = None
        # Expand/collapse state keyed by _Item.expand_key().
        self._expanded: set[str] = {f"path:{STROKES_ROOT}"}
        # Auto-counter for new "Group N" names.
        self._group_name_counter = 1
        # User-clicked row keys (any rows, not just groups). Drives the blue
        # selection border. Ctrl+click toggles individual rows.
        self._selected_row_keys: set[str] = set()
        # Keyboard subscription used to clear selection on ESC.
        self._keyboard_sub: Any = None

    # BUILD THE PANEL'S CONTENTS INSIDE THE GIVEN FRAME.
    def build(self, frame: ui.Frame) -> None:
        self._frame = frame
        with frame:
            with ui.VStack(spacing=0):
                # Header / footer each get a trailing Spacer that grows to
                # the scrollbar width when the list is overflowing, so all
                # three rows end at the same x.
                with ui.HStack(height=_ROW_HEIGHT):
                    self._build_header()
                    self._header_gutter = ui.Spacer(width=ui.Pixel(0))
                self._scroll_frame = ui.ScrollingFrame(
                    height=ui.Fraction(1),
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                )
                with self._scroll_frame:
                    self._list_frame = ui.Frame()
                self._rebuild_list()
                with ui.HStack(height=_BUTTON_BAR_HEIGHT):
                    self._build_button_bar()
                    self._footer_gutter = ui.Spacer(width=ui.Pixel(0))
        self._scroll_frame.set_computed_content_size_changed_fn(self._sync_gutter)
        self._sync_gutter()
        self._wire_listeners()
        self._subscribe_keyboard()

    def _sync_gutter(self) -> None:
        w = _SCROLLBAR_GUTTER if self._scroll_frame.scroll_y_max > 0 else ui.Pixel(0)
        self._header_gutter.width = w
        self._footer_gutter.width = w

    # TEAR DOWN LISTENERS.
    def destroy(self) -> None:
        self._unsubscribe_keyboard()
        lock_mgr = LockManager.get()
        if lock_mgr is not None and self._lock_cb is not None:
            lock_mgr.remove_listener(self._lock_cb)
        vis_mgr = VisibilityManager.get()
        if vis_mgr is not None and self._vis_cb is not None:
            vis_mgr.remove_listener(self._vis_cb)
        registry = LayerRegistry.get()
        if registry is not None and self._registry_cb is not None:
            registry.remove_listener(self._registry_cb)
        groups = GroupRegistry.get()
        if groups is not None and self._group_cb is not None:
            groups.remove_listener(self._group_cb)
        sync = SelectionSync.get()
        if sync is not None and self._selection_cb is not None:
            sync.remove_listener(self._selection_cb)
        self._lock_cb = None
        self._vis_cb = None
        self._registry_cb = None
        self._group_cb = None
        self._selection_cb = None
        self._list_frame = None
        self._frame = None

    # ----- internals -----

    # SUBSCRIBE TO ALL FOUR MANAGERS SO THE LIST AUTO-REFRESHES.
    def _wire_listeners(self) -> None:
        # Listener callbacks fire from inside event/draw cycles (e.g. when a
        # plane row is clicked, set_active_plane() notifies us mid-event).
        # Calling Frame.clear() during a draw is illegal in omni.ui, so we
        # bounce off asyncio to rebuild on the next frame instead.
        self._lock_cb = self._schedule_rebuild
        self._vis_cb = self._schedule_rebuild
        self._registry_cb = self._schedule_rebuild
        self._group_cb = self._schedule_rebuild
        self._selection_cb = self._on_stage_selection_changed
        lock_mgr = LockManager.get()
        if lock_mgr is not None:
            lock_mgr.add_listener(self._lock_cb)
        vis_mgr = VisibilityManager.get()
        if vis_mgr is not None:
            vis_mgr.add_listener(self._vis_cb)
        registry = LayerRegistry.get()
        if registry is not None:
            registry.add_listener(self._registry_cb)
        groups = GroupRegistry.get()
        if groups is not None:
            groups.add_listener(self._group_cb)
        sync = SelectionSync.get()
        if sync is not None:
            sync.add_listener(self._selection_cb)
            # Reflect whatever the stage already has selected.
            self._on_stage_selection_changed()

    # MIRROR STAGE SELECTION INTO THE C_LAYERS BLUE-BORDER SELECTION.
    # A stage selection of a child prim (e.g. the mesh under a drawing
    # plane Xform) still highlights the matching C_Layers row. Group-row
    # selections live only in the panel (they have no stage equivalent),
    # so we preserve any existing "group:*" keys here.
    def _on_stage_selection_changed(self) -> None:
        sync = SelectionSync.get()
        if sync is None:
            return
        selected_paths = sync.current_paths()
        item_paths = [it.path for it in self._collect_items() if it.path]
        new_path_keys: set[str] = set()
        for sp in selected_paths:
            for ip in item_paths:
                if sp == ip or sp.startswith(ip + "/"):
                    new_path_keys.add(f"path:{ip}")
        group_keys = {k for k in self._selected_row_keys if k.startswith("group:")}
        new_keys = new_path_keys | group_keys
        if new_keys != self._selected_row_keys:
            self._selected_row_keys = new_keys
            self._schedule_rebuild()

    # SUBSCRIBE TO THE GLOBAL KEYBOARD SO ESC CAN CLEAR THE SELECTION.
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
            LOGGER.warning("CLayers: could not subscribe to keyboard events: %s", exc)
            self._keyboard_sub = None

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

    # CLEAR THE SELECTION ON ESC. RETURN TRUE TO LET OTHER SUBSCRIBERS
    # (e.g. tool deactivation, hotkeys, camera flight mode) STILL SEE
    # THE EVENT.
    def _on_keyboard_event(self, event: Any) -> bool:
        try:
            event_type = event.type
            key = event.input
        except Exception:
            return True
        if (
            event_type == carb.input.KeyboardEventType.KEY_PRESS
            and key == carb.input.KeyboardInput.ESCAPE
        ):
            if self._selected_row_keys:
                self._selected_row_keys.clear()
                groups = GroupRegistry.get()
                if groups is not None and groups.selected_group() is not None:
                    groups.set_selected_group(None)
                else:
                    self._schedule_rebuild()
        return True

    # COLUMN HEADER ROW.
    def _build_header(self) -> None:
        def _disclosure_cell() -> None:
            self._cell(_DISCLOSURE_WIDTH, lambda: None)

        def _name_cell() -> None:
            self._cell(
                _NAME_WIDTH,
                lambda: ui.Label("Name", style=_HEADER_LABEL_STYLE, alignment=ui.Alignment.LEFT_CENTER),
            )

        def _type_cell() -> None:
            self._cell(
                _TYPE_WIDTH,
                lambda: ui.Label("Type", style=_HEADER_LABEL_STYLE, alignment=ui.Alignment.LEFT_CENTER),
            )

        def _vis_cell() -> None:
            self._cell(
                _VIS_WIDTH,
                lambda: ui.Label("V", style=_HEADER_LABEL_STYLE, alignment=ui.Alignment.CENTER),
                centered=True,
            )

        def _lock_cell() -> None:
            self._cell(
                _LOCK_WIDTH,
                lambda: ui.Label("L", style=_HEADER_LABEL_STYLE, alignment=ui.Alignment.CENTER),
                centered=True,
            )

        def _active_cell() -> None:
            self._cell(
                _ACTIVE_WIDTH,
                lambda: ui.Label("A", style=_HEADER_LABEL_STYLE, alignment=ui.Alignment.CENTER),
                centered=True,
            )

        self._build_columns(
            background_style=_HEADER_STYLE,
            disclosure_fn=_disclosure_cell,
            name_fn=_name_cell,
            type_fn=_type_cell,
            vis_fn=_vis_cell,
            lock_fn=_lock_cell,
            active_fn=_active_cell,
        )

    # SHARED COLUMN SCAFFOLD USED BY BOTH THE HEADER AND EVERY DATA ROW.
    # The outer HStack contains one fixed-width inner HStack per column,
    # so the column boundaries are IDENTICAL between header and rows.
    def _build_columns(
        self,
        background_style: dict[str, Any],
        disclosure_fn: Any,
        name_fn: Any,
        type_fn: Any,
        vis_fn: Any,
        lock_fn: Any,
        active_fn: Any,
        overlay_fn: Any | None = None,
    ) -> None:
        with ui.ZStack(height=_ROW_HEIGHT):
            ui.Rectangle(style=background_style)
            with ui.HStack():
                ui.Spacer(width=ui.Pixel(8))
                disclosure_fn()
                ui.Spacer(width=ui.Pixel(4))
                name_fn()
                type_fn()
                vis_fn()
                lock_fn()
                active_fn()
                ui.Spacer()
            if overlay_fn is not None:
                overlay_fn()

    # GENERIC FIXED-WIDTH CELL: an inner HStack of `width` whose children
    # are arranged either left-aligned (default: content + trailing Spacer)
    # or centered (leading Spacer + content + trailing Spacer). Returns
    # the widget produced by content_fn so callers can attach handlers.
    def _cell(self, width: ui.Length, content_fn: Any, centered: bool = False) -> Any:
        with ui.HStack(width=width, height=_ROW_HEIGHT):
            if centered:
                ui.Spacer()
            widget = content_fn()
            ui.Spacer()
        return widget

    # BOTTOM BUTTON BAR (ADD GROUP / GROUP / UNGROUP).
    def _build_button_bar(self) -> None:
        with ui.ZStack(height=_BUTTON_BAR_HEIGHT):
            ui.Rectangle(style=_BUTTON_BAR_STYLE)
            with ui.HStack(spacing=4):
                ui.Spacer(width=ui.Pixel(6))
                self._build_action_button(_ICON_ADD_GROUP, "Add group", self._on_add_group_clicked)
                self._build_action_button(_ICON_GROUP, "Group selected", self._on_group_clicked)
                self._build_action_button(_ICON_UNGROUP, "Ungroup", self._on_ungroup_clicked)
                ui.Spacer()

    # SINGLE ICON BUTTON USED BY THE BAR.
    def _build_action_button(self, icon: str, tooltip: str, handler: Any) -> None:
        button = ui.Button(
            "",
            image_url=icon,
            image_width=_BUTTON_ICON_VISUAL,
            image_height=_BUTTON_ICON_VISUAL,
            width=ui.Pixel(28),
            height=_ROW_HEIGHT,
            tooltip=tooltip,
            style=_BUTTON_STYLE,
            clicked_fn=handler,
        )
        # Avoid pylance "unused" warning.
        _ = button

    # ----- list rendering -----

    # COLLECT ALL ROWS RECURSIVELY (TOP-LEVEL FIRST, GROUPS NESTED).
    def _collect_items(self) -> list[_Item]:
        items: list[_Item] = []
        items.append(_Item(_KIND_PLANE, "Ground Plane", path=DEFAULT_GROUND_PLANE_PATH))

        registry = LayerRegistry.get()
        groups = GroupRegistry.get()
        stage = cast(Any, omni.usd.get_context()).get_stage()
        if registry is None or stage is None:
            return items

        # Top-level groups (sibling to ungrouped drawing planes / strokes).
        if groups is not None:
            for gid in groups.child_groups_of(GROUP_ANCHOR_ROOT):
                self._emit_group_rows(items, gid, indent=0)
            # Also surface any orphaned groups that ended up parented to the
            # legacy ThreeDDraw anchor so they remain reachable.
            for gid in groups.child_groups_of(GROUP_ANCHOR_THREEDDRAW):
                self._emit_group_rows(items, gid, indent=0)

        # Ungrouped drawing planes.
        for path in registry.drawing_planes():
            if groups is not None and groups.group_of_path(path) is not None:
                continue
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                items.append(_Item(_KIND_PLANE, prim.GetName(), path=path, prim=prim))

        # Ungrouped strokes (the ThreeDDraw Xform itself is no longer shown).
        for path in registry.strokes():
            if groups is not None and groups.group_of_path(path) is not None:
                continue
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                items.append(_Item(_KIND_STROKE, prim.GetName(), path=path, prim=prim))
        return items

    # APPEND A GROUP ROW PLUS, WHEN EXPANDED, ITS CHILD GROUPS AND MEMBERS.
    def _emit_group_rows(self, items: list[_Item], gid: str, indent: int) -> None:
        groups = GroupRegistry.get()
        if groups is None:
            return
        stage = cast(Any, omni.usd.get_context()).get_stage()
        if stage is None:
            return
        item = _Item(_KIND_GROUP, groups.get_name(gid), indent=indent, group_id=gid)
        items.append(item)
        if item.expand_key() not in self._expanded:
            return
        # Nested child groups first, then member prims.
        for child_gid in groups.child_groups_of(gid):
            self._emit_group_rows(items, child_gid, indent + 1)
        for path in groups.members_of(gid):
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            kind = _KIND_PLANE if path.startswith("/World/DrawingPlanes") else _KIND_STROKE
            items.append(_Item(kind, prim.GetName(), indent=indent + 1, path=path, prim=prim))

    # CLEAR AND REPOPULATE THE LIST FRAME WITH CURRENT STATE.
    def _rebuild_list(self) -> None:
        if self._list_frame is None:
            return
        items = self._collect_items()
        registry = LayerRegistry.get()
        active_path = registry.active_plane() if registry is not None else DEFAULT_GROUND_PLANE_PATH
        self._list_frame.clear()
        with self._list_frame:
            with ui.VStack(spacing=0):
                for item in items:
                    self._build_row(item, active_path)
                ui.Spacer()

    # Listener-safe variant: defers the rebuild to the next app update so
    # we never call Frame.clear() while omni.ui is iterating its widgets.
    # Coalesces multiple notifications fired in the same frame into one
    # rebuild via the _rebuild_pending flag.
    def _schedule_rebuild(self) -> None:
        if self._list_frame is None:
            return
        if getattr(self, "_rebuild_pending", False):
            return
        self._rebuild_pending = True

        async def _do() -> None:
            try:
                await cast(Any, omni.kit.app.get_app()).next_update_async()
            except Exception:
                pass
            self._rebuild_pending = False
            try:
                self._rebuild_list()
            except Exception:
                LOGGER.exception("CLayers: deferred _rebuild_list failed")

        try:
            asyncio.ensure_future(_do())
        except Exception:
            self._rebuild_pending = False

    # BUILD A SINGLE ROW.
    def _build_row(self, item: _Item, active_path: str) -> None:
        is_plane = item.kind == _KIND_PLANE
        is_active_plane = is_plane and item.path == active_path
        is_active_group = item.kind == _KIND_GROUP and self._group_active(item.group_id)
        is_selected_row = self._row_key(item) in self._selected_row_keys
        if is_active_group:
            row_style = _ROW_ACTIVE_GROUP_STYLE
        elif is_active_plane:
            row_style = _ROW_ACTIVE_PLANE_STYLE
        else:
            row_style = _ROW_STYLE

        def _disclosure() -> None:
            self._build_disclosure_cell(item)

        def _name() -> None:
            self._build_name_cell(item)

        def _type_label() -> None:
            self._cell(
                _TYPE_WIDTH,
                lambda: ui.Label(item.kind, style=_LABEL_STYLE, alignment=ui.Alignment.LEFT_CENTER),
            )

        def _vis() -> None:
            self._build_visibility_cell(item)

        def _lock() -> None:
            self._build_lock_cell(item)

        def _active() -> None:
            self._build_active_cell(item, is_active_plane, is_active_group)

        def _overlay() -> None:
            if is_selected_row:
                ui.Rectangle(style=_ROW_SELECTION_BORDER_STYLE)

        self._build_columns(
            background_style=row_style,
            disclosure_fn=_disclosure,
            name_fn=_name,
            type_fn=_type_label,
            vis_fn=_vis,
            lock_fn=_lock,
            active_fn=_active,
            overlay_fn=_overlay,
        )

    # NAME CELL: CLICKING TOGGLES THE BLUE ROW SELECTION; FOR GROUP ROWS IT
    # ALSO TOGGLES THE GroupRegistry SELECTION USED BY THE UNGROUP BUTTON.
    # INDENT IS BAKED IN HERE (INSIDE THE NAME CELL) SO THE COLUMNS THAT
    # FOLLOW STAY AT THEIR FIXED POSITIONS REGARDLESS OF NESTING.
    def _build_name_cell(self, item: _Item) -> None:
        with ui.HStack(width=_NAME_WIDTH, height=_ROW_HEIGHT):
            # One thin vertical line per indent level, just like a code editor.
            for _ in range(item.indent):
                ui.Rectangle(width=_INDENT_GUIDE_WIDTH, style=_INDENT_GUIDE_STYLE)
                ui.Spacer(width=ui.Pixel(_INDENT_PER_LEVEL - 1))
            label = ui.Label(
                item.name,
                style=_LABEL_STYLE,
                alignment=ui.Alignment.LEFT_CENTER,
            )
            ui.Spacer()
        row_key = self._row_key(item)
        gid = item.group_id if item.kind == _KIND_GROUP else None

        def _select(_x: float, _y: float, button: int, modifier: int) -> None:
            if button != 0:
                return
            ctrl_held = bool(modifier & int(carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL))
            already = row_key in self._selected_row_keys
            if ctrl_held:
                # Toggle this row in the existing selection.
                if already:
                    self._selected_row_keys.discard(row_key)
                else:
                    self._selected_row_keys.add(row_key)
            else:
                # Plain click: replace selection (or clear if it was the
                # only selected row).
                if already and len(self._selected_row_keys) == 1:
                    self._selected_row_keys.clear()
                else:
                    self._selected_row_keys = {row_key}
            # Sync the GroupRegistry single-selection used by the Ungroup
            # button: it tracks the most recently picked group (or None).
            if gid is not None:
                groups = GroupRegistry.get()
                if groups is not None:
                    if row_key in self._selected_row_keys:
                        groups.set_selected_group(gid)
                    else:
                        # If this group is no longer selected and was the
                        # GroupRegistry's pick, clear that pick.
                        if groups.selected_group() == gid:
                            groups.set_selected_group(None)
            # Mirror our row selection to the USD stage selection so the
            # 3D viewport highlights the same prims.
            self._push_selection_to_stage()
            self._schedule_rebuild()

        cast(Any, label).set_mouse_pressed_fn(_select)

    # DISCLOSURE GUTTER. SHOWS PLUS/MINUS ON COLLAPSIBLE ROWS, BLANK ELSEWHERE.
    def _build_disclosure_cell(self, item: _Item) -> None:
        if item.kind not in (_KIND_XFORM, _KIND_GROUP):
            ui.Spacer(width=_DISCLOSURE_WIDTH)
            return
        key = item.expand_key()
        expanded = key in self._expanded
        icon = _ICON_MINUS if expanded else _ICON_PLUS
        image = self._centered_icon(icon, _DISCLOSURE_WIDTH, _DISCLOSURE_VISUAL)

        def _toggle() -> None:
            if key in self._expanded:
                self._expanded.discard(key)
            else:
                self._expanded.add(key)
            self._schedule_rebuild()

        def _pressed(_x: float, _y: float, button: int, _m: int) -> None:
            if button == 0:
                _toggle()
        cast(Any, image).set_mouse_pressed_fn(_pressed)

    # VISIBILITY COLUMN. USD-BACKED ROWS AND GROUP ROWS BOTH PARTICIPATE.
    def _build_visibility_cell(self, item: _Item) -> None:
        if item.kind == _KIND_GROUP and item.group_id is not None:
            groups = GroupRegistry.get()
            visible = groups.is_visible_self(item.group_id) if groups is not None else True
            icon = _ICON_EYE_ON if visible else _ICON_EYE_OFF
            image = self._centered_icon(icon, _VIS_WIDTH, _ICON_VISUAL, centered=True)
            gid = item.group_id

            def _group_pressed(_x: float, _y: float, button: int, _m: int) -> None:
                if button != 0:
                    return
                mgr = GroupRegistry.get()
                if mgr is None:
                    return
                mgr.set_visible(gid, not mgr.is_visible_self(gid))
            cast(Any, image).set_mouse_pressed_fn(_group_pressed)
            return
        if item.prim is None:
            ui.Spacer(width=_VIS_WIDTH)
            return
        vis_mgr = VisibilityManager.get()
        visible = vis_mgr.is_visible(item.prim) if vis_mgr is not None else True
        icon = _ICON_EYE_ON if visible else _ICON_EYE_OFF
        image = self._centered_icon(icon, _VIS_WIDTH, _ICON_VISUAL, centered=True)
        prim = item.prim

        def _click() -> None:
            mgr = VisibilityManager.get()
            if mgr is None:
                return
            mgr.set_visible(prim, not mgr.is_visible_self(prim))

        def _pressed(_x: float, _y: float, button: int, _m: int) -> None:
            if button == 0:
                _click()
        cast(Any, image).set_mouse_pressed_fn(_pressed)

    # LOCK COLUMN. USD-BACKED ROWS AND GROUP ROWS BOTH PARTICIPATE.
    def _build_lock_cell(self, item: _Item) -> None:
        if item.kind == _KIND_GROUP and item.group_id is not None:
            groups = GroupRegistry.get()
            locked = groups.is_locked_self(item.group_id) if groups is not None else False
            icon = _ICON_LOCKED if locked else _ICON_UNLOCKED
            image = self._centered_icon(icon, _LOCK_WIDTH, _ICON_VISUAL, centered=True)
            gid = item.group_id

            def _group_pressed(_x: float, _y: float, button: int, _m: int) -> None:
                if button != 0:
                    return
                mgr = GroupRegistry.get()
                if mgr is None:
                    return
                mgr.set_locked(gid, not mgr.is_locked_self(gid))
            cast(Any, image).set_mouse_pressed_fn(_group_pressed)
            return
        if item.prim is None:
            ui.Spacer(width=_LOCK_WIDTH)
            return
        lock_mgr = LockManager.get()
        locked = lock_mgr.is_locked_self(item.prim) if lock_mgr is not None else False
        icon = _ICON_LOCKED if locked else _ICON_UNLOCKED
        image = self._centered_icon(icon, _LOCK_WIDTH, _ICON_VISUAL, centered=True)
        prim = item.prim

        def _click() -> None:
            mgr = LockManager.get()
            if mgr is None:
                return
            mgr.set_locked(prim, not mgr.is_locked_self(prim))

        def _pressed(_x: float, _y: float, button: int, _m: int) -> None:
            if button == 0:
                _click()
        cast(Any, image).set_mouse_pressed_fn(_pressed)

    # ACTIVE COLUMN. DRAWING PLANES (SINGLE-ACTIVE GLOBALLY) AND GROUPS
    # (SINGLE-ACTIVE PER PARENT) PARTICIPATE.
    def _build_active_cell(self, item: _Item, is_active_plane: bool, is_active_group: bool) -> None:
        if item.kind == _KIND_PLANE:
            icon = _ICON_ACTIVE_ON if is_active_plane else _ICON_ACTIVE_OFF
            image = self._centered_icon(icon, _ACTIVE_WIDTH, _ICON_VISUAL, centered=True)
            path = item.path

            def _plane_click() -> None:
                registry = LayerRegistry.get()
                if registry is None:
                    return
                registry.set_active_plane(path)

            def _plane_pressed(_x: float, _y: float, button: int, _m: int) -> None:
                if button == 0:
                    _plane_click()
            cast(Any, image).set_mouse_pressed_fn(_plane_pressed)
            return

        if item.kind == _KIND_GROUP and item.group_id is not None:
            icon = _ICON_ACTIVE_ON if is_active_group else _ICON_ACTIVE_OFF
            image = self._centered_icon(icon, _ACTIVE_WIDTH, _ICON_VISUAL, centered=True)
            gid = item.group_id

            def _group_click() -> None:
                groups = GroupRegistry.get()
                if groups is None:
                    return
                groups.set_active(gid)

            def _group_pressed(_x: float, _y: float, button: int, _m: int) -> None:
                if button == 0:
                    _group_click()
            cast(Any, image).set_mouse_pressed_fn(_group_pressed)
            return

        ui.Spacer(width=_ACTIVE_WIDTH)

    # LEFT-ALIGNS A SQUARE ICON INSIDE A FIXED-WIDTH ROW-HEIGHT CELL.
    # Pass centered=True for V/L/A icons so they sit under their centered
    # header labels.
    def _centered_icon(
        self, icon: str, cell_width: ui.Length, icon_size: ui.Length, centered: bool = False
    ) -> ui.Image:
        def _make() -> ui.Image:
            with ui.VStack(width=icon_size):
                ui.Spacer()
                image = ui.Image(
                    icon,
                    width=icon_size,
                    height=icon_size,
                    fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
                )
                ui.Spacer()
            return image
        return self._cell(cell_width, _make, centered=centered)

    # IS THIS GROUP ID CURRENTLY THE ACTIVE SIBLING UNDER ITS PARENT?
    def _group_active(self, group_id: str | None) -> bool:
        if group_id is None:
            return False
        groups = GroupRegistry.get()
        return bool(groups.is_active(group_id)) if groups is not None else False

    # STABLE KEY USED TO REMEMBER WHICH ROW THE USER CLICKED.
    def _row_key(self, item: _Item) -> str:
        if item.group_id is not None:
            return f"group:{item.group_id}"
        return f"path:{item.path}"

    # ----- button handlers -----

    # PARSE _selected_row_keys ("path:<p>" / "group:<gid>") INTO TWO LISTS.
    def _parse_selection(self) -> tuple[list[str], list[str]]:
        paths: list[str] = []
        gids: list[str] = []
        for key in self._selected_row_keys:
            if key.startswith("path:"):
                paths.append(key[len("path:"):])
            elif key.startswith("group:"):
                gids.append(key[len("group:"):])
        return paths, gids

    # PUSH THE C_LAYERS ROW SELECTION TO THE USD STAGE. PATH-ROWS SELECT
    # THEIR PRIM; GROUP-ROWS SELECT EVERY MEMBER PRIM RECURSIVELY.
    def _push_selection_to_stage(self) -> None:
        sync = SelectionSync.get()
        if sync is None:
            return
        paths, gids = self._parse_selection()
        groups = GroupRegistry.get()
        all_paths: list[str] = list(paths)
        if groups is not None:
            for gid in gids:
                for p in self._collect_group_paths(gid, groups):
                    if p not in all_paths:
                        all_paths.append(p)
        sync.push(all_paths)

    # ALL USD PATHS REACHABLE FROM A GROUP (MEMBERS + NESTED CHILD GROUPS).
    def _collect_group_paths(self, gid: str, groups: Any) -> list[str]:
        out: list[str] = list(groups.members_of(gid))
        for child in groups.child_groups_of(gid):
            for p in self._collect_group_paths(child, groups):
                if p not in out:
                    out.append(p)
        return out

    # PARENT GROUP/ANCHOR FOR "SAME LEVEL AS THE FIRST SELECTED ROW".
    def _parent_of_selection(self) -> str:
        groups = GroupRegistry.get()
        if groups is None:
            return GROUP_ANCHOR_ROOT
        paths, gids = self._parse_selection()
        if gids:
            return groups.parent_of(gids[0]) or GROUP_ANCHOR_ROOT
        if paths:
            return groups.group_of_path(paths[0]) or GROUP_ANCHOR_ROOT
        return GROUP_ANCHOR_ROOT

    # ALLOCATE A NEW "Group N" NAME.
    def _next_group_name(self) -> str:
        name = f"Group {self._group_name_counter}"
        self._group_name_counter += 1
        return name

    # ADD AN EMPTY GROUP AT THE SAME LEVEL AS THE SELECTED ROW (OR ROOT).
    def _on_add_group_clicked(self) -> None:
        groups = GroupRegistry.get()
        if groups is None:
            return
        new_id = groups.create_group(self._next_group_name(), self._parent_of_selection())
        groups.set_selected_group(new_id)

    # WRAP THE C_LAYERS-SELECTED PATHS INTO A NEW GROUP. THE NEW GROUP IS
    # PLACED AT THE SAME LEVEL THE FIRST SELECTED ITEM CURRENTLY LIVES AT.
    def _on_group_clicked(self) -> None:
        groups = GroupRegistry.get()
        if groups is None:
            return
        paths, _gids = self._parse_selection()
        if not paths:
            return
        new_id = groups.create_group(
            self._next_group_name(),
            self._parent_of_selection(),
            member_paths=paths,
        )
        groups.set_selected_group(new_id)

    # UNGROUP THE CURRENTLY CLAYERS-SELECTED GROUP.
    def _on_ungroup_clicked(self) -> None:
        groups = GroupRegistry.get()
        if groups is None:
            return
        gid = groups.selected_group()
        if gid is None:
            return
        groups.ungroup(gid)
