import omni.ui as ui

from ..active_context import (
    add_active_context_listener,
    get_active_usd_context,
    remove_active_context_listener,
)
from ..startup.styles import AGENT_WINDOW_BACKGROUND_STYLE
from ..tools.three_d_draw.drawing_plane import DrawingPlane
from ..tools.three_d_draw.basis_curves import ThreeDDrawCurvesTool
from ..tools.three_d_draw.escape_handler import EscapeHandler
from ..tools.three_d_draw.three_d_draw import ThreeDDrawTool
from ..tools.tool import Tool
from ..tools.transform.transform_tools import (
    OP_SELECT,
    TRANSFORM_OP_SETTING,
    TransformTools,
)
from .window import LauncherWindow


class BottomWindow(LauncherWindow):
    def __init__(self) -> None:
        # Tool instances kept alive so their callbacks stay valid.
        self._drawing_plane = DrawingPlane()
        self._transform_tools = TransformTools()
        self._three_d_draw = ThreeDDrawTool()
        self._three_d_draw_curves = ThreeDDrawCurvesTool()
        self._tools: list[Tool] = [
            *self._transform_tools.make_tools(),
            self._three_d_draw.make_tool(),
            self._three_d_draw_curves.make_tool(),
            self._drawing_plane.make_tool(),
        ]

        # On viewport-tab switch, reset all stateful tool state so a tool
        # left active in one tab never carries into another. Also clear
        # the new active context's selection so each tab starts clean.
        add_active_context_listener(self._on_active_context_changed)

        # Global Esc handler so pressing Esc deselects any active tool
        # (Move / Rotate / Scale / Draw) in EVERY viewport. Kit's own
        # transform manipulator only listens to the global context's
        # viewport, so without this our toolbar buttons stay highlighted
        # when the user is in a named-context viewport tab.
        self._esc_handler = EscapeHandler()
        self._esc_handler.subscribe(self._on_escape)

        # Create the docked Tools panel at the bottom of the layout.
        super().__init__(
            title="Tools",
            height=60,
        )

    # Drop the active-context listener and Esc subscription when the
    # window is torn down so we don't leak callbacks into a dead window.
    def destroy(self) -> None:
        try:
            self._esc_handler.unsubscribe()
        except Exception:
            pass
        remove_active_context_listener(self._on_active_context_changed)
        super().destroy()

    # Esc anywhere in the app: deactivate every stateful tool and reset
    # the transform-op selector to plain Select. This mirrors what Kit's
    # built-in viewport does, but works for every viewport tab regardless
    # of which UsdContext it is bound to.
    def _on_escape(self) -> None:
        self._reset_tool_state()

    # Reset all stateful tools and clear the new active context's selection.
    def _on_active_context_changed(self) -> None:
        self._reset_tool_state()
        # Also clear the new active context's selection so the new tab
        # opens with a clean slate. Only done on tab switch, not on Esc.
        try:
            ctx = get_active_usd_context()
            if ctx is not None:
                ctx.get_selection().clear_selected_prim_paths()
        except Exception:
            pass

    # Shared "drop every active tool" path used by both Esc and tab-switch.
    def _reset_tool_state(self) -> None:
        # Deactivate the freehand draw tools if they're active.
        try:
            self._three_d_draw.deactivate()
        except Exception:
            pass
        try:
            self._three_d_draw_curves.deactivate()
        except Exception:
            pass
        # Force the Transform-op selector back to plain Select so Move /
        # Rotate / Scale buttons drop their highlighted state.
        try:
            import carb.settings

            settings = carb.settings.get_settings()
            settings.set_string(TRANSFORM_OP_SETTING, OP_SELECT)
        except Exception:
            pass


    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                # Match the Explorer's dark background.
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                with ui.VStack():
                    ui.Spacer(height=6)
                    with ui.HStack(spacing=6):
                        ui.Spacer(width=8)
                        for tool in self._tools:
                            tool.build_button()
                        ui.Spacer()
