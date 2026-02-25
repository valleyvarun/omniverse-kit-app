import asyncio
from typing import Any, cast

import omni.ext
import omni.kit.app
import omni.ui as ui


class MyExtension(omni.ext.IExt):
    def __init__(self):
        super().__init__()
        self._background_window: ui.Window | None = None
        self._dock_task: asyncio.Task[None] | None = None

    def on_startup(self, ext_id: str) -> None:
        print("[Mega Launcher] Background UI Starting")

        # 1. Restrictions of main window
        my_flags = (ui.WINDOW_FLAGS_NO_SCROLLBAR |    # Hides scrollbars
                    ui.WINDOW_FLAGS_NO_CLOSE)         # User cannot close it

        # 2. CREATE THE WINDOW
        self._background_window = ui.Window("Background", flags=my_flags)
        self._dock_task = asyncio.ensure_future(self._dock_background_window())

        # 3. BUILD THE UI CONTENT
        with self._background_window.frame:
            # ZStack allows us to layer a background color behind everything
            with ui.ZStack():

                # Layer 1: Dark background color
                ui.Rectangle(style={"background_color": 0xFF222222})

                # Layer 2: Main Content
                with ui.VStack(spacing=20):
                    ui.Spacer(height=50)

                    ui.Label("I am the new Workspace Root",
                             alignment=ui.Alignment.CENTER,
                             style={"font_size": 30, "color": 0xFF888888})

                    ui.Button("I look like I belong here", height=50)
    def on_shutdown(self) -> None:
        if self._dock_task and not self._dock_task.done():
            self._dock_task.cancel()
        self._dock_task = None
        self._background_window = None

    async def _dock_background_window(self) -> None:
        app = cast(Any, omni.kit.app.get_app())

        for _ in range(120):
            await app.next_update_async()

            if not self._background_window:
                return

            self._background_window.deferred_dock_in(
                "Content", ui.DockPolicy.CURRENT_WINDOW_IS_ACTIVE
            )

            content_window = ui.Workspace.get_window("Content")
            if content_window:
                self._background_window.dock_in(content_window, ui.DockPosition.SAME)
                self._background_window.focus()
                return
