"""Arrange existing windows for the standalone drawing application."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.ext
import omni.kit.actions.core
import omni.kit.app
import omni.ui

from .widths import SidebarWidths, load_widths, save_widths

EXTENSION_ID: str = "varun.drawing.layout.v.01"
LAYOUT_PATH: Path = Path(__file__).resolve().parents[1] / "layouts" / "default.json"
WINDOW_TITLES: tuple[str, ...] = ("Tool Box", "Viewport", "Clayers", "Stage")


class DrawingLayoutExtension(omni.ext.IExt):
    def __init__(self) -> None:
        super().__init__()
        self._layout_task: asyncio.Future[None] | None = None
        self._widths_path: Path | None = None
        self._update_subscription: Any = None
        self._shutdown_subscription: Any = None
        self._ready: bool = False
        self._last_widths: SidebarWidths | None = None
        self._saved_widths: SidebarWidths | None = None
        self._changed_at: float = 0.0
        self._dpi_scale: float = omni.ui.Workspace.get_dpi_scale()

    # Wait for the existing extensions' windows before restoring the docks.
    def on_startup(self, _ext_id: str) -> None:
        settings: Any = carb.settings.get_settings()
        test_path: str = str(settings.get("/app/drawing/testWidthsFile") or "")
        self._widths_path = Path(test_path) if test_path else LAYOUT_PATH.with_name("sidebar-widths.json")
        self._saved_widths = load_widths(self._widths_path)
        registry: Any = omni.kit.actions.core.get_action_registry()
        registry.register_action(
            EXTENSION_ID,
            "reset_layout",
            self.reset_layout,
            display_name="Reset Drawing Layout",
            description="Restore the three-column drawing workspace.",
        )
        self._request_layout(self._saved_widths)
        app: Any = omni.kit.app.get_app()
        self._update_subscription = app.get_update_event_stream().create_subscription_to_pop(
            self._on_update, name="3d-drawing sidebar widths"
        )
        self._shutdown_subscription = app.get_shutdown_event_stream().create_subscription_to_pop(
            self._on_quit, name="3d-drawing save sidebar widths"
        )

    def reset_layout(self) -> None:
        self._request_layout(None)

    def _request_layout(self, widths: SidebarWidths | None) -> None:
        self._ready = False
        if self._layout_task is not None:
            self._layout_task.cancel()
        self._layout_task = asyncio.ensure_future(self._restore_layout(widths))

    async def _restore_layout(self, widths: SidebarWidths | None) -> None:
        app: Any = omni.kit.app.get_app()
        title: str
        for title in WINDOW_TITLES:
            omni.ui.Workspace.show_window(title, True)
        frame: int
        for frame in range(120):
            if all(omni.ui.Workspace.get_window(title) is not None for title in WINDOW_TITLES):
                break
            await app.next_update_async()
        else:
            raise RuntimeError("The drawing workspace could not find its required windows.")

        layout: Any = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        # A second restore wins over delayed docking from Stage and the viewport.
        for frame in range(2):
            await app.next_update_async()
            cast(Any, omni.ui.Workspace).restore_workspace(layout, True)
            delay: int
            for delay in range(20):
                await app.next_update_async()
        for title in WINDOW_TITLES:
            window: Any = omni.ui.Workspace.get_window(title)
            window.dock_tab_bar_enabled = False
            window.dock_tab_bar_visible = False
        viewport: Any = omni.ui.Workspace.get_window("Viewport")
        if widths is not None:
            await self._apply_widths(widths)
        viewport.focus()
        self._ready = True

    # Preserve room for the viewport if the app reopens on a smaller display.
    async def _apply_widths(self, widths: SidebarWidths) -> None:
        left: Any = omni.ui.Workspace.get_window("Tool Box")
        right: Any = omni.ui.Workspace.get_window("Clayers")
        center: Any = omni.ui.Workspace.get_window("Viewport")
        total: float = 0.0
        window: Any
        for window in (left, center, right):
            total += omni.ui.Workspace.get_dock_id_width(window.dock_id)
        available: float = max(160.0, total - 320.0)
        scale: float = min(1.0, available / (widths.left + widths.right))
        target: SidebarWidths = SidebarWidths(max(80.0, widths.left * scale), max(80.0, widths.right * scale))
        left_request: float = target.left
        right_request: float = target.right
        app: Any = omni.kit.app.get_app()
        attempt: int
        # Compensate for dock splitter rounding after each layout update.
        for attempt in range(6):
            omni.ui.Workspace.set_dock_id_width(right.dock_id, right_request)
            omni.ui.Workspace.set_dock_id_width(left.dock_id, left_request)
            await app.next_update_async()
            actual: SidebarWidths | None = self._current_widths()
            if actual is None:
                return
            left_error: float = target.left - actual.left
            right_error: float = target.right - actual.right
            if abs(left_error) <= 0.75 and abs(right_error) <= 0.75:
                return
            left_request += left_error
            right_request += right_error

    def _current_widths(self) -> SidebarWidths | None:
        left: Any = omni.ui.Workspace.get_window("Tool Box")
        right: Any = omni.ui.Workspace.get_window("Clayers")
        if not left or not right or not left.visible or not right.visible or not left.docked or not right.docked:
            return None
        return SidebarWidths(
            round(omni.ui.Workspace.get_dock_id_width(left.dock_id), 1),
            round(omni.ui.Workspace.get_dock_id_width(right.dock_id), 1),
        )

    # Save after dragging stops, including in Kit's fast-shutdown mode.
    def _on_update(self, _event: Any) -> None:
        scale: float = omni.ui.Workspace.get_dpi_scale()
        if scale != self._dpi_scale:
            self._dpi_scale = scale
            # Remeasure text and controls after moving between monitor DPIs.
            style: Any = omni.ui.Style.get_instance()
            style.default = style.default
            if self._ready and self._last_widths is not None:
                self._ready = False
                self._layout_task = asyncio.ensure_future(self._restore_sidebar_widths(self._last_widths))
        if not self._ready:
            return
        widths: SidebarWidths | None = self._current_widths()
        if widths is None:
            return
        if widths != self._last_widths:
            self._last_widths = widths
            self._changed_at = time.monotonic()
        elif widths != self._saved_widths and time.monotonic() - self._changed_at >= 0.5:
            self._save_widths(widths)

    async def _restore_sidebar_widths(self, widths: SidebarWidths) -> None:
        await self._apply_widths(widths)
        self._ready = True

    def _save_widths(self, widths: SidebarWidths) -> None:
        if self._widths_path is not None and save_widths(self._widths_path, widths):
            self._saved_widths = widths
        self._changed_at = time.monotonic()

    def _on_quit(self, _event: Any) -> None:
        if self._ready:
            widths: SidebarWidths | None = self._current_widths()
            if widths is not None and widths != self._saved_widths:
                self._save_widths(widths)

    def on_shutdown(self) -> None:
        self._on_quit(None)
        self._update_subscription = None
        self._shutdown_subscription = None
        self._ready = False
        if self._layout_task is not None:
            self._layout_task.cancel()
            self._layout_task = None
        registry: Any = omni.kit.actions.core.get_action_registry()
        registry.deregister_all_actions_for_extension(EXTENSION_ID)
