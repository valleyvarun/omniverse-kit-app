
import asyncio
from typing import Any, cast

import omni.ext
import omni.kit.app
import omni.usd
from omni import ui
from omni.kit import stage_templates
from omni.kit.viewport.window import ViewportWindow


class MyExtension(omni.ext.IExt):

    # ON STARTUP
    def on_startup(self, _ext_id: str):
        print("[varun.launcher.usd_viewport.v.01] Extension startup")

        # no header, not movable
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # create USD viewport
        self._window = ViewportWindow(
            "Viewport",
            width=1015,
            height=560,
            flags=flags,
        )
        asyncio.ensure_future(self._create_stage())

    # create initial USD stage
    async def _create_stage(self):
        kit_app = cast(Any, omni.kit.app.get_app())
        for _ in range(5):
            await kit_app.next_update_async()

        if omni.usd.get_context().can_open_stage():
            cast(Any, stage_templates).new_stage(template="empty")

    # ON SHUTDOWN
    def on_shutdown(self):
        print("[varun.launcher.usd_viewport.v.01] Extension shutdown")
        self._window.destroy()
