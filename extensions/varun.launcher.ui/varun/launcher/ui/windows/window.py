from typing import Any

import omni.ui as ui


class LauncherWindow:
    # Create window shell.
    def __init__(
        self,
        title: str,
        width: int = 0,
        height: int = 0,
        flags: Any = 0,
    ) -> None:
        # Store the window title and create the Omni UI window shell.
        self.window_title = title
        self._window: ui.Window | None = ui.Window(
            title,
            width=width,
            height=height,
            flags=flags,
        )

        # Let each concrete window class populate its own contents.
        self._build_ui()

    # Build subclass UI.
    def _build_ui(self) -> None:
        pass

    # Release window state.
    def destroy(self) -> None:
        # Release the window reference when the panel is torn down.
        self._window = None