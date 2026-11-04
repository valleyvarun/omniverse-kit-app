"""Real Kit input/menu checks; run with interactions.kit --exec this-file.

All reports and captures are separate from smoke.py, under _build/interactions.
This test clicks rendered widgets and sends input events; it never calls a menu
callback or changes theme settings directly to manufacture a passing result.
"""

# Python & library imports
import asyncio
import json
import logging
import traceback
from pathlib import Path
from typing import Any, TypeAlias, cast

# Omniverse & USD imports
import carb.input
import carb.settings
import omni.kit.app
import omni.kit.renderer.capture
from omni import ui
from omni.kit import ui_test
from omni.kit.ui_test.input import emulate_keyboard, emulate_mouse
from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport_window
from omni.kit.viewport.window import ViewportWindow
from omni.kit.widget.viewport.api import ViewportAPI
from pxr import Gf, Usd, UsdGeom

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

ROOT: str = "/exts/varun.launcher.usd_viewport.v.01/viewportLight"
THEME: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme"
OUTPUT: Path = Path(__file__).resolve().parents[1] / "_build" / "interactions"
ReportValue: TypeAlias = bool | str | list[str] | list[list[str | float]] | dict[str, float] | dict[str, list[float]]
REPORT: dict[str, ReportValue] = {}
LOGGER: logging.Logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Wait for Kit UI updates to complete.
async def settle(count: int = 15) -> None:
    await ui_test.wait_n_updates(count)


# Save a screenshot after the next rendered frame.
async def capture(name: str) -> None:
    interface: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
    interface.capture_next_frame_swapchain(str(OUTPUT / f"{name}.png"))
    await settle(5)


# Collect visible labels from nested menus.
def menu_labels(menu: ui.Menu) -> list[str]:
    labels: list[str] = []
    child: ui.Widget
    for child in ui.Inspector.get_children(menu):
        if isinstance(child, (ui.Menu, ui.MenuItem)):
            if child.visible:
                labels.append(child.text)
            if isinstance(child, ui.Menu):
                labels.extend(menu_labels(child))
    return labels


# Open the renderer selector through a real mouse click.
async def open_renderer_menu() -> ui.Menu:
    candidates: list[Any] = ui_test.find_all("Viewport//Frame/**/Menu[*]")
    item: Any
    labels: list[str] = []
    renderer: Any = None
    for item in candidates:
        label: str = cast(ui.Menu, item.widget).text
        labels.append(label)
        if label in ("Viewport Light", "Storm"):
            renderer = item
    REPORT["viewport_menu_labels"] = labels
    assert renderer is not None, f"Renderer dropdown not found: {REPORT['viewport_menu_labels']}"
    await ui_test.emulate_mouse_move_and_click(renderer.position + ui_test.Vec2(30, 10))
    await settle(8)
    menu: ui.Menu = cast(ui.Menu, renderer.widget)
    assert menu.shown, "Renderer menu did not open through a click"
    return menu


# Open the existing lighting button after its Light-mode menu adapter is applied.
async def open_lighting_menu() -> ui.Menu:
    candidates: list[Any] = ui_test.find_all("Viewport//Frame/**/Menu[*]")
    item: Any
    lighting: Any = None
    for item in candidates:
        if item.widget.identifier == "ViewportLightThemeMenu":
            lighting = item
            break
    assert lighting is not None, "Light viewport lighting dropdown not found"
    await ui_test.emulate_mouse_move_and_click(lighting.position + ui_test.Vec2(20, 10))
    await settle(8)
    menu: ui.Menu = cast(ui.Menu, lighting.widget)
    assert menu.shown, "Lighting menu did not open through a click"
    return menu


# Navigate Create, Mesh, and Sphere using mouse input.
async def click_create_sphere(menu: ui.Menu) -> None:
    name: str
    child: ui.Widget
    for name in ("Create", "Mesh", "Sphere"):
        item: ui.Menu | ui.MenuItem | None = None
        for child in ui.Inspector.get_children(menu):
            if isinstance(child, (ui.Menu, ui.MenuItem)) and child.text == name:
                item = child
                break
        assert item is not None, f"Create menu item {name} was not found"
        positions: list[list[str | float]] = cast(
            list[list[str | float]], REPORT.setdefault("create_widget_positions", [])
        )
        positions.append([name, item.screen_position_x, item.screen_position_y])
        await ui_test.emulate_mouse_move(ui_test.Vec2(item.screen_position_x + 65, item.screen_position_y + 10))
        await asyncio.sleep(0.5)
        await settle(10)
        await capture(f"create_{name}")
        if isinstance(item, ui.Menu):
            menu = item
        else:
            await ui_test.emulate_mouse_click()


def mesh_paths(stage: Usd.Stage) -> set[str]:
    paths: set[str] = set()
    prim: Usd.Prim
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            paths.add(str(prim.GetPath()))
    return paths


# Check renderer menus, theme clicks, mesh creation, and camera navigation.
async def checks() -> None:

    # --- 1) PREPARE THE VIEWPORT AND TEST CUBE ---

    await settle(50)
    settings: Any = carb.settings.get_settings()
    assert settings.get(f"{ROOT}/ready"), "Native viewport policy did not load"
    title: str
    for title in ("Render Settings",):
        panel: ui.WindowHandle | None = ui.Workspace.get_window(title)
        if panel:
            panel.visible = False
    window: ViewportWindow | None = get_active_viewport_window()
    assert window is not None, "Viewport window missing"
    window.visible = True
    window.width, window.height = 1180, 760
    window.position_x, window.position_y = 0, 28
    window.focus()
    api: ViewportAPI = window.viewport_api
    context: Any = api.usd_context
    await context.new_stage_async()
    await settle()
    stage: Usd.Stage | None = context.get_stage()
    assert stage is not None
    UsdGeom.Cube.Define(stage, "/World/Cube").CreateSizeAttr(2)
    frame_viewport_prims(api, ["/World/Cube"])
    await settle(30)
    assert api.hydra_engine == "pxr" and api.render_mode == "HdStormRendererPlugin"

    # --- 2) CHECK THE RENDERER MENU AND THEME CLICKS ---

    menu: ui.Menu = await open_renderer_menu()
    labels: list[str] = menu_labels(menu)
    REPORT["renderer_menu_choices"] = labels
    assert "Viewport Light" in labels
    assert "RTX - Real-Time 2.0" in labels
    assert "Viewport Light Appearance" not in labels
    assert "Light mode" not in labels and "Dark mode" not in labels
    await capture("renderer_menu")

    # Themes are the only two choices on the existing lighting button.
    label: str
    theme: str
    for label, theme in (("Dark mode", "dark"), ("Light mode", "light")):
        menu = await open_lighting_menu()
        labels = menu_labels(menu)
        assert labels == ["Light mode", "Dark mode"], labels
        REPORT["lighting_menu_choices"] = labels
        await ui_test.select_context_menu(label, menu_root=menu)
        await settle(20)
        assert settings.get(THEME) == theme, f"Click did not select {theme}"
        assert settings.get(f"{ROOT}/studio") == ("dark_studio" if theme == "dark" else "grey_studio")
        REPORT[f"{theme}_appearance_click"] = True
        await capture(theme)

    # --- 3) CREATE A SPHERE THROUGH THE CONTEXT MENU ---

    # Leave room for two nested Create submenus without reversing their direction.
    position: ui_test.Vec2 = ui_test.Vec2(120, 240)
    await ui_test.emulate_mouse_move_and_click(position, right_click=True)
    await settle(12)
    context_menu: ui.Menu | None = ui.Menu.get_current()
    assert context_menu is not None and context_menu.shown, "Right-click menu did not open"
    labels = menu_labels(context_menu)
    REPORT["right_click_choices"] = labels
    assert "Create" in labels, "Create submenu missing"
    await capture("create_menu")
    existing_meshes: set[str] = mesh_paths(stage)
    await click_create_sphere(context_menu)
    await settle(20)
    created: set[str] = mesh_paths(stage) - existing_meshes
    assert created, "Create/Mesh/Sphere click did not create a mesh"
    REPORT["created_mesh_paths"] = sorted(created)

    # --- 4) CHECK FLIGHT NAVIGATION KEYS ---

    # Flight uses the existing right-drag gesture and six default navigation keys.
    # A small initial drag enters the gesture before measuring translation.
    candidate: Any
    navigation: Any = None
    for candidate in ui_test.find_all("Viewport//Frame/**/Button[*]"):
        widget: ui.Widget = candidate.widget
        if isinstance(widget, ui.Button) and widget.text == "Mouse: Orbit":
            navigation = candidate
            break
    if navigation is not None:
        await ui_test.emulate_mouse_move_and_click(navigation.position + ui_test.Vec2(20, 10))
        await settle(10)
    await ui_test.emulate_mouse_move(position)
    await emulate_mouse(carb.input.MouseEventType.RIGHT_BUTTON_DOWN, position)
    await settle(3)
    await ui_test.emulate_mouse_move(position + ui_test.Vec2(8, 0))
    await settle(5)
    movements: dict[str, float] = {}
    translations: dict[str, list[float]] = {}
    name: str
    try:
        for name in "WASDQE":
            key: carb.input.KeyboardInput = getattr(carb.input.KeyboardInput, name)
            before: list[float] = list(api.frame_info["view"])
            await emulate_keyboard(carb.input.KeyboardEventType.KEY_PRESS, key)
            await settle(25)
            await emulate_keyboard(carb.input.KeyboardEventType.KEY_RELEASE, key)
            await settle(8)
            after: list[float] = list(api.frame_info["view"])
            a: float
            b: float
            delta: float = 0.0
            for a, b in zip(after, before):
                delta = max(delta, abs(float(a) - float(b)))
            movements[name] = delta
            assert delta > 1e-6, f"{name} did not move the viewport camera"
            world_delta: Gf.Vec3d = (
                Gf.Matrix4d(*after).GetInverse().ExtractTranslation()
                - Gf.Matrix4d(*before).GetInverse().ExtractTranslation()
            )
            translations[name] = list(world_delta)
            await settle(20)
    finally:
        # Release every synthetic key and mouse button even on failure.
        for name in "WASDQE":
            await emulate_keyboard(carb.input.KeyboardEventType.KEY_RELEASE, getattr(carb.input.KeyboardInput, name))
        await emulate_mouse(carb.input.MouseEventType.RIGHT_BUTTON_UP, position)
    REPORT["navigation_camera_deltas"] = movements
    REPORT["navigation_world_displacements"] = translations
    forward: str
    backward: str
    for forward, backward in (("W", "S"), ("A", "D"), ("Q", "E")):
        assert (
            Gf.Dot(Gf.Vec3d(*translations[forward]), Gf.Vec3d(*translations[backward])) < 0
        ), f"{forward}/{backward} did not move in opposite directions"
    REPORT["same_viewport_window"] = get_active_viewport_window() is window
    assert REPORT["same_viewport_window"]
    await settle()
    await capture("after_navigation")


# Run interaction checks and save the pass or failure report.
async def run() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.wait_for(checks(), timeout=120)
        REPORT["passed"] = True
    except Exception:
        LOGGER.exception("Viewport Light interaction checks failed")
        REPORT["passed"] = False
        REPORT["error"] = traceback.format_exc()
        await capture("failure")
    finally:
        # Write the interaction report and return its result code.
        (OUTPUT / "report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
        print("VIEWPORT_LIGHT_INTERACTIONS " + json.dumps(REPORT), flush=True)
        omni.kit.app.get_app().post_quit(0 if REPORT.get("passed") else 1)


# ----------------------------------------------------------------------
# 3. MODULE SETUP
# ----------------------------------------------------------------------

asyncio.ensure_future(run())
