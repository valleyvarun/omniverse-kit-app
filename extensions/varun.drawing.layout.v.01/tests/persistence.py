"""Run in a disposable portable app twice, with testPhase=record then restore."""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import carb.settings
import omni.kit.actions.core
import omni.kit.app
import omni.ui

LOGGER: logging.Logger = logging.getLogger(__name__)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    settings.set("/app/file/ignoreUnsavedOnExit", True)
    phase: str = str(settings.get("/app/drawing/testPhase"))
    test_path: str = str(settings.get("/app/drawing/testWidthsFile") or "")
    assert test_path, "Set /app/drawing/testWidthsFile to a disposable project-local JSON file"
    saved: Path = Path(test_path)
    report: dict[str, Any] = {"phase": phase, "file": str(saved)}

    async def settle(seconds: float = 1.0) -> None:
        deadline: float = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await app.next_update_async()

    def widths() -> tuple[float, float]:
        left: Any = omni.ui.Workspace.get_window("Tool Box")
        right: Any = omni.ui.Workspace.get_window("Clayers")
        return (
            omni.ui.Workspace.get_dock_id_width(left.dock_id),
            omni.ui.Workspace.get_dock_id_width(right.dock_id),
        )

    def resize(left_width: float, right_width: float) -> None:
        left: Any = omni.ui.Workspace.get_window("Tool Box")
        right: Any = omni.ui.Workspace.get_window("Clayers")
        omni.ui.Workspace.set_dock_id_width(right.dock_id, right_width)
        omni.ui.Workspace.set_dock_id_width(left.dock_id, left_width)

    def check(left: float, right: float) -> None:
        actual: tuple[float, float] = widths()
        assert abs(actual[0] - left) <= 1 and abs(actual[1] - right) <= 1, actual
        data: Any = json.loads(saved.read_text(encoding="utf-8"))
        assert abs(data["left"] - left) <= 1 and abs(data["right"] - right) <= 1, data

    try:
        await settle(4.0)
        if phase == "record":
            registry: Any = omni.kit.actions.core.get_action_registry()
            registry.execute_action("varun.drawing.layout.v.01", "reset_layout")
            await settle(2.0)
            report["defaults"] = widths()
            resize(210.0, 285.0)
            await settle()
            check(*widths())
            report["saved_after_resize"] = True
            # Quit before the debounce expires to exercise the shutdown flush.
            resize(220.0, 295.0)
            await app.next_update_async()
            report["before_quit"] = widths()
        else:
            assert phase == "restore"
            previous: Any = json.loads((saved.parent / "persistence-record.json").read_text(encoding="utf-8"))
            check(*previous["before_quit"])
            report["restored_across_processes"] = True
            registry = omni.kit.actions.core.get_action_registry()
            registry.execute_action("varun.drawing.layout.v.01", "reset_layout")
            await settle(2.0)
            check(*previous["defaults"])
            report["reset_saves_defaults"] = True
        report["passed"] = True
    except Exception:
        LOGGER.exception("Sidebar width persistence check failed")
        report["passed"] = False
    finally:
        saved.parent.mkdir(parents=True, exist_ok=True)
        (saved.parent / f"persistence-{phase}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("SIDEBAR_PERSISTENCE " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
