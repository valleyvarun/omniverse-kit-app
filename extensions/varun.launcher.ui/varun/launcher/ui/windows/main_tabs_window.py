"""Custom 'fake' tab bar shown above the main dock slot."""

from typing import Any, Callable, cast

import asyncio
from pathlib import Path

import omni.kit.app
import omni.ui as ui


# Close (X) icon path; bundled with the extension logos.
_CLOSE_ICON = str(
    (Path(__file__).resolve().parent.parent / "logos" / "close.svg")
)


# Tab geometry -- matched to Kit's native dock-tab bar.
_TAB_HEIGHT = 20
_TAB_CHAR_WIDTH = 7
_TAB_FONT_SIZE = 14
_TAB_SIDE_PADDING = 8
# Leading-space padding for closable tab labels (Button padding_x is unreliable).
_TAB_LABEL_LEFT_PAD_CHARS = 4
# Right-side reservation on closable tabs for the X overlay.
_X_BUTTON_SIZE = 18
_X_LABEL_GAP = -8
_X_RIGHT_MARGIN = 4

# AARRGGBB colors matched to Kit's native dock-tab bar.
_COLOR_BAR_BG = 0xFF1A1A1A
_COLOR_TAB_INACTIVE = 0xFF333333
_COLOR_TAB_INACTIVE_HOVER = 0xFF2A2A2A
_COLOR_TAB_ACTIVE = 0xFF454545
_COLOR_LABEL_INACTIVE = 0xFF9A9A9A
_COLOR_LABEL_ACTIVE = 0xFFFFFFFF


# Inactive tab style (square corners; 1px gaps come from HStack spacing).
_TAB_BUTTON_STYLE: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": _COLOR_TAB_INACTIVE,
        "border_radius": 0,
        "padding": 0,
        "margin": 0,
    },
    "Button.Label": {
        "color": _COLOR_LABEL_INACTIVE,
        "font_size": _TAB_FONT_SIZE,
    },
    "Button:hovered": {
        "background_color": _COLOR_TAB_INACTIVE_HOVER,
    },
}

# Active tab style (background merges with the content below).
_TAB_BUTTON_ACTIVE_STYLE: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": _COLOR_TAB_ACTIVE,
        "border_radius": 0,
        "padding": 0,
        "margin": 0,
    },
    "Button.Label": {
        "color": _COLOR_LABEL_ACTIVE,
        "font_size": _TAB_FONT_SIZE,
    },
    "Button:hovered": {
        "background_color": _COLOR_TAB_ACTIVE,
    },
}

# Closable-tab styles left-align the label to leave room for the X.
_TAB_BUTTON_STYLE_CLOSABLE: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": _COLOR_TAB_INACTIVE,
        "border_radius": 0,
        "padding": 0,
        "margin": 0,
    },
    "Button.Label": {
        "color": _COLOR_LABEL_INACTIVE,
        "font_size": _TAB_FONT_SIZE,
        "alignment": ui.Alignment.LEFT_CENTER,
    },
    "Button:hovered": {
        "background_color": _COLOR_TAB_INACTIVE_HOVER,
    },
}
_TAB_BUTTON_ACTIVE_STYLE_CLOSABLE: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": _COLOR_TAB_ACTIVE,
        "border_radius": 0,
        "padding": 0,
        "margin": 0,
    },
    "Button.Label": {
        "color": _COLOR_LABEL_ACTIVE,
        "font_size": _TAB_FONT_SIZE,
        "alignment": ui.Alignment.LEFT_CENTER,
    },
    "Button:hovered": {
        "background_color": _COLOR_TAB_ACTIVE,
    },
}

# Per-tab close (X) button. Two styles: hidden (fully transparent) and
# visible. We swap STYLES rather than toggling `visible` so the button
# always occupies its full 18px in layout -- otherwise the Spacer-sandwich
# that vertically centers it re-allocates when visibility flips, causing
# the X to jump 1-2px on hover.
_X_BUTTON_STYLE_HIDDEN: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": 0x00000000,
        "border_radius": 3,
        "padding": 0,
        "margin": 0,
        "alignment": ui.Alignment.CENTER,
    },
    "Button.Image": {
        "color": 0x00000000,
    },
}
_X_BUTTON_STYLE_VISIBLE: dict[str, dict[str, Any]] = {
    "Button": {
        "background_color": 0x00000000,
        "border_radius": 3,
        "padding": 0,
        "margin": 0,
        "alignment": ui.Alignment.CENTER,
    },
    "Button.Image": {
        "color": _COLOR_LABEL_INACTIVE,
    },
    "Button:hovered": {
        "background_color": 0xFF3D3D3D,
    },
    "Button.Image:hovered": {
        "color": _COLOR_LABEL_ACTIVE,
    },
}

class MainTabsWindow:
    """Docked window that draws the fake tab strip.

    ``on_tab_clicked`` fires when a tab is clicked; ``on_tab_close``
    fires when the X overlay on a closable tab is clicked.
    """

    def __init__(
        self,
        on_tab_clicked: Callable[[str], None],
        on_tab_close: Callable[[str], None] | None = None,
    ) -> None:
        self._on_tab_clicked = on_tab_clicked
        self._on_tab_close = on_tab_close
        self._tab_names: list[str] = []
        self._closable: dict[str, bool] = {}
        self._active: str | None = None

        # No chrome -- the window is just a row of buttons; docking stays enabled.
        flags = (
            ui.WINDOW_FLAGS_NO_TITLE_BAR
            | ui.WINDOW_FLAGS_NO_SCROLLBAR
            | ui.WINDOW_FLAGS_NO_RESIZE
        )
        self._window: ui.Window | None = ui.Window(
            "Main Tabs",
            height=_TAB_HEIGHT,
            flags=flags,
            padding_x=0,
            padding_y=0,
        )
        # Hide Kit's own dock-tab bar on this window's dock node.
        try:
            cast(Any, self._window).dock_tab_bar_visible = False
            cast(Any, self._window).dock_tab_bar_enabled = False
        except Exception:
            pass
        # Strip the implicit Window frame padding.
        try:
            cast(Any, self._window.frame).set_style(
                {
                    "Window": {
                        "padding": 0,
                        "margin": 0,
                        "background_color": _COLOR_BAR_BG,
                    },
                    "Frame": {
                        "padding": 0,
                        "margin": 0,
                        "background_color": _COLOR_BAR_BG,
                    },
                }
            )
        except Exception:
            pass

        self._window.frame.set_build_fn(self._build_contents)

        # Force the dock-node height on every frame so hot-reload picks up changes.
        asyncio.ensure_future(self._enforce_dock_height())

    async def _enforce_dock_height(self) -> None:
        app = cast(Any, omni.kit.app.get_app())
        while self._window is not None:
            await app.next_update_async()
            win: Any = self._window
            if win is None:
                return
            dock_id = win.dock_id
            if dock_id is None or dock_id < 0:
                continue
            try:
                cast(Any, ui.Workspace).set_dock_id_height(dock_id, _TAB_HEIGHT)
            except Exception:
                return

    # PUBLIC API ----------------------------------------------------------

    @property
    def window(self) -> ui.Window | None:
        """The underlying ``ui.Window``. Exposed for docking lookups."""
        return self._window

    def add_tab(self, name: str, closable: bool = True) -> None:
        """Append a tab button. Idempotent. ``closable=False`` skips the X overlay."""
        if name in self._tab_names:
            return
        self._tab_names.append(name)
        self._closable[name] = closable
        if self._active is None:
            self._active = name
        self._refresh()

    def remove_tab(self, name: str) -> None:
        """Remove a tab. No-op if it isn't present."""
        if name not in self._tab_names:
            return
        self._tab_names.remove(name)
        self._closable.pop(name, None)
        if self._active == name:
            self._active = self._tab_names[0] if self._tab_names else None
        self._refresh()

    def set_active(self, name: str) -> None:
        """Update which tab is shown as active. No-op if unchanged."""
        if name not in self._tab_names:
            return
        if self._active == name:
            return
        self._active = name
        self._refresh()

    # LIFECYCLE -----------------------------------------------------------

    def destroy(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        self._tab_names.clear()
        self._closable.clear()
        self._active = None

    # INTERNAL ------------------------------------------------------------

    def _refresh(self) -> None:
        if self._window is None:
            return
        try:
            self._window.frame.rebuild()
        except Exception:
            pass

    def _build_contents(self) -> None:
        # All containers pinned to _TAB_HEIGHT so font metrics don't grow the dock node.
        with ui.ZStack(height=_TAB_HEIGHT):
            ui.Rectangle(
                style={"background_color": _COLOR_BAR_BG, "border_width": 0}
            )
            with ui.HStack(spacing=1, height=_TAB_HEIGHT):
                for name in self._tab_names:
                    self._build_tab(name)
                ui.Spacer()

    def _build_tab(self, name: str) -> None:
        is_active = name == self._active
        closable = self._closable.get(name, True)
        if closable:
            style = (
                _TAB_BUTTON_ACTIVE_STYLE_CLOSABLE
                if is_active
                else _TAB_BUTTON_STYLE_CLOSABLE
            )
        else:
            style = _TAB_BUTTON_ACTIVE_STYLE if is_active else _TAB_BUTTON_STYLE

        # Non-closable tab (e.g. Home): plain centered button.
        if not closable:
            width = max(60, len(name) * _TAB_CHAR_WIDTH + 2 * _TAB_SIDE_PADDING)
            ui.Button(
                name,
                width=width,
                height=_TAB_HEIGHT,
                style=style,
                clicked_fn=lambda n=name: self._handle_click(n),
            )
            return

        # Closable: LEFT-aligned label (with leading-space padding) + X overlay on the right.
        display_name = (" " * _TAB_LABEL_LEFT_PAD_CHARS) + name
        label_width = len(display_name) * _TAB_CHAR_WIDTH
        width = (
            label_width
            + _X_LABEL_GAP
            + _X_BUTTON_SIZE
            + _X_RIGHT_MARGIN
        )
        width = max(70, width)

        with ui.ZStack(width=width, height=_TAB_HEIGHT):
            tab_btn = ui.Button(
                display_name,
                width=width,
                height=_TAB_HEIGHT,
                style=style,
                clicked_fn=lambda n=name: self._handle_click(n),
            )
            # X overlay via Placer so it only occupies its own footprint
            # (HStack-spanning overlays swallow clicks for the whole tab).
            # Spacer-sandwich vertically centers inside the ACTUAL rendered
            # tab cell (which is slightly taller than _TAB_HEIGHT due to
            # window chrome), so we don't hard-code an offset_y.
            # The button is ALWAYS visible=True with a transparent style;
            # hover swaps to the visible style. This keeps layout stable.
            x_offset = width - _X_BUTTON_SIZE - _X_RIGHT_MARGIN
            with ui.Placer(offset_x=x_offset, offset_y=0, draggable=False):
                with ui.VStack(width=_X_BUTTON_SIZE, height=_TAB_HEIGHT):
                    ui.Spacer()
                    x_btn = ui.Button(
                        "",
                        image_url=_CLOSE_ICON,
                        image_width=_X_BUTTON_SIZE - 4,
                        image_height=_X_BUTTON_SIZE - 4,
                        width=_X_BUTTON_SIZE,
                        height=_X_BUTTON_SIZE,
                        style=_X_BUTTON_STYLE_HIDDEN,
                    )
                    # Use mouse-press (fires on down) instead of clicked_fn
                    # (fires on release) so the close runs BEFORE the tab
                    # button beneath fires its own click-on-release, which
                    # would otherwise rebuild the strip and cancel us.
                    def _on_x_press(x: float, y: float, btn: int, mod: int, n: str = name) -> None:
                        if btn == 0:
                            self._handle_close(n)
                    try:
                        cast(Any, x_btn).set_mouse_pressed_fn(_on_x_press)
                    except Exception:
                        pass
                    ui.Spacer()

        # Track hover on both tab and X so moving cursor between them doesn't flicker the X.
        hover = {"tab": False, "x": False}

        def _update(_x: ui.Button = x_btn) -> None:
            try:
                cast(Any, _x).set_style(
                    _X_BUTTON_STYLE_VISIBLE
                    if (hover["tab"] or hover["x"])
                    else _X_BUTTON_STYLE_HIDDEN
                )
            except Exception:
                pass

        def _on_tab_hover(is_hovered: bool) -> None:
            hover["tab"] = bool(is_hovered)
            _update()

        def _on_x_hover(is_hovered: bool) -> None:
            hover["x"] = bool(is_hovered)
            _update()

        try:
            cast(Any, tab_btn).set_mouse_hovered_fn(_on_tab_hover)
        except Exception:
            pass
        try:
            cast(Any, x_btn).set_mouse_hovered_fn(_on_x_hover)
        except Exception:
            pass

    def _handle_click(self, name: str) -> None:
        try:
            self._on_tab_clicked(name)
        except Exception:
            pass

    def _handle_close(self, name: str) -> None:
        if self._on_tab_close is None:
            return
        try:
            self._on_tab_close(name)
        except Exception:
            pass
