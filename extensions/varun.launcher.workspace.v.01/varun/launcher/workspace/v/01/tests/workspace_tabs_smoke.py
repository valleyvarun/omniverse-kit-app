"""Run inside New Launcher with --exec; uses only temporary USD files."""

# Python & library imports
import asyncio
import gc
import importlib
import json
import logging
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

# Omniverse & USD imports
import omni.kit.app
import omni.usd
from omni.kit.viewport.utility import get_viewport_from_window_name
from pxr import Gf, Usd, UsdGeom

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Check independent tab stages, saved views, and close behavior.
async def run():

    # --- 1) PREPARE THE REPORT AND TEST HELPERS ---

    app: Any = omni.kit.app.get_app()
    context: Any = omni.usd.get_context()
    output = Path(__file__).resolve().parents[9] / "_build/workspace_tabs"
    output.mkdir(parents=True, exist_ok=True)
    report = {}

    # Wait for asynchronous layout and stage changes.
    async def settle(count=35):
        for _ in range(count):
            await app.next_update_async()

    try:
        await settle(60)
        cls = importlib.import_module("varun.launcher.workspace.v.01.extension").MyExtension
        workspace = next(obj for obj in gc.get_objects() if isinstance(obj, cls))
        viewport: Any = get_viewport_from_window_name("Viewport")
        views = {}

        # Set and remember a distinct camera view for each tab.
        def set_view(tab_id, path, position, rotation, zoom):
            viewport.camera_path = path
            stage = context.get_stage()
            camera = UsdGeom.Camera.Get(stage, path)
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                camera.GetPrim().GetAttribute("xformOp:translate").Set(Gf.Vec3d(*position))
                camera.GetPrim().GetAttribute("xformOp:rotateXYZ").Set(Gf.Vec3d(*rotation))
                camera.GetFocalLengthAttr().Set(zoom)
                camera.GetHorizontalApertureAttr().Set(zoom * 2)
                camera.GetPrim().GetAttribute("omni:kit:centerOfInterest").Set(Gf.Vec3d(0, 0, -123))
            views[tab_id] = (path, camera.GetCamera())

        # Check the tab's camera, projection, and zoom after switching.
        def check_view(tab_id):
            path, expected = views[tab_id]
            assert str(viewport.camera_path) == path, "Selected camera leaked from another tab"
            actual = UsdGeom.Camera.Get(context.get_stage(), path).GetCamera()
            assert Gf.IsClose(actual.transform, expected.transform, 1e-6), "Camera pose reset on tab switch"
            assert abs(actual.focalLength - expected.focalLength) < 1e-5
            assert abs(actual.horizontalAperture - expected.horizontalAperture) < 1e-5
            assert actual.projection == expected.projection

        # Select a tab and wait until its stage is attached.
        async def select(tab_id: int) -> None:
            workspace._select_tab(tab_id)
            deadline: float = time.monotonic() + 20
            while workspace._tasks or not context.can_open_stage():
                assert time.monotonic() < deadline, "Tab switch did not finish within 20 seconds"
                await app.next_update_async()
            await settle()
            assert workspace._active_tab_id == tab_id
            if tab_id:
                assert context.get_stage() == workspace._tabs[tab_id].stage

        # --- 2) CREATE TEMPORARY STAGE FILES ---

        with tempfile.TemporaryDirectory(dir=output) as directory:
            paths = [str(Path(directory) / f"{name}.usda") for name in ("A", "B")]
            for path, prim in zip(paths, ("OnlyA", "OnlyB")):
                fixture = Usd.Stage.CreateNew(path)
                UsdGeom.Cube.Define(fixture, f"/{prim}")
                fixture.GetRootLayer().Save()
                del fixture

            # --- 3) CHECK FILE, EMPTY, AND UNSAVED TABS ---

            first = workspace.add_viewport_tab()
            await settle()
            assert (await context.open_stage_async(paths[0]))[0]
            await settle()
            UsdGeom.Sphere.Define(context.get_stage(), "/UnsavedA")
            first_stage = workspace._tabs[first].stage
            set_view(first, "/OmniverseKit_Persp", (123, 456, 789), (15, 35, 5), 64)

            empty = workspace.add_viewport_tab()
            await settle()
            assert bool(first_stage), "Opened file stage expired on tab switch"
            assert not context.get_stage().GetPrimAtPath("/OnlyA")
            assert workspace._tabs[empty].file_path is None
            UsdGeom.Cube.Define(context.get_stage(), "/OnlyEmpty")

            second = workspace.add_viewport_tab()
            await settle()
            assert (await context.open_stage_async(paths[1]))[0]
            await settle()
            set_view(second, "/OmniverseKit_Top", (11, 900, 33), (-90, 0, 0), 80)
            for tab_id, present, absent in (
                (first, "/OnlyA", "/OnlyB"),
                (empty, "/OnlyEmpty", "/OnlyA"),
                (second, "/OnlyB", "/OnlyEmpty"),
            ):
                await select(tab_id)
                assert context.get_stage().GetPrimAtPath(present)
                assert not context.get_stage().GetPrimAtPath(absent)
                if tab_id in views:
                    check_view(tab_id)
            report["per_tab_camera_pose_projection_and_zoom"] = True
            await select(first)
            assert context.get_stage().GetPrimAtPath("/UnsavedA")
            assert workspace._tabs[first].file_path == paths[0].replace("\\", "/")
            report["separate_files_empty_and_unsaved_edits"] = True

            # --- 4) CHECK SAVE AS AND RAPID SWITCHING ---

            saved_path = str(Path(directory) / "A_saved.usda")
            assert (await context.save_as_stage_async(saved_path))[0]
            await settle()
            assert workspace._tabs[first].tab_id == first
            assert workspace._tabs[first].file_path == saved_path.replace("\\", "/")
            # Save As may reload the stage. Test later tab switches against the
            # view immediately after that file operation, not its previous stage.
            views[first] = (
                str(viewport.camera_path),
                UsdGeom.Camera.Get(context.get_stage(), viewport.camera_path).GetCamera(),
            )
            report["save_as_keeps_id_and_updates_file"] = True

            # Exercise an in-flight switch followed by another selection.
            for tab_id in (second, first, empty, first, second, empty):
                workspace._select_tab(tab_id)
                await app.next_update_async()
            await settle(60)
            assert workspace._context_tab_id == empty
            assert context.get_stage().GetPrimAtPath("/OnlyEmpty")
            assert workspace._tabs[first].stage.GetPrimAtPath("/OnlyA")
            report["rapid_switches"] = True

            # --- 5) CHECK NEW, HOME, AND TAB CLOSURE ---

            # File > New must replace only the attached tab's scene association.
            await select(second)
            assert await context.new_stage_async()
            await settle()
            assert workspace._tabs[second].file_path is None
            assert not workspace._tabs[second].stage.GetPrimAtPath("/OnlyB")
            await select(first)
            assert context.get_stage().GetPrimAtPath("/UnsavedA")
            await select(0)
            await select(first)
            assert context.get_stage().GetPrimAtPath("/OnlyA")
            check_view(first)
            report["new_and_home_preserve_other_tabs"] = True

            workspace._close_tab(workspace._tabs[empty])
            assert workspace._active_tab_id == first
            assert workspace._tabs[second].tab_id == second
            workspace._close_tab(workspace._tabs[first])
            await settle()
            newest = workspace.add_viewport_tab()
            await settle()
            assert newest > second
            assert first not in workspace._tabs and empty not in workspace._tabs
            assert len(workspace._stage_cache.GetAllStages()) == 2
            report["permanent_ids_and_cache_cleanup"] = True
            report["ids"] = [first, empty, second, newest]

            # Close a requested tab while the shared context is switching to it.
            await select(second)
            workspace._select_tab(newest)
            await app.next_update_async()
            workspace._close_tab(workspace._tabs[newest])
            await settle(60)
            assert set(workspace._tabs) == {0, second}
            assert workspace._context_tab_id == second
            report["close_during_switch"] = True
        # Record success only after every tab check completes.
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Workspace tab check failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        # Write the report and close the disposable stage before exiting.
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("WORKSPACE_TABS " + json.dumps(report), flush=True)
        await context.close_stage_async()
        app.post_quit(0 if report.get("passed") else 1)


# ----------------------------------------------------------------------
# 2. MODULE SETUP
# ----------------------------------------------------------------------

asyncio.ensure_future(run())
