"""Exercise Explorer in New Launcher with --enable omni.kit.ui_test --exec this-file."""

import asyncio
import gc
import importlib
import json
import logging
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import carb.input
import omni.kit.app
import omni.kit.renderer.capture
import omni.usd
from omni import ui
from omni.kit import ui_test


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    output: Path = Path(__file__).resolve().parents[4] / "_build/explorer_checks"
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    extension: Any = None

    def check_layout() -> None:
        left: Any = ui.Workspace.get_window("Explorer")
        assert left.dock_id > 0, "Explorer became undocked"
        title: str
        for title in ("home", "tabs_bar_window", "Tool Box"):
            window: Any = ui.Workspace.get_window(title)
            assert window.position_x >= left.position_x + left.width, f"Explorer overlaps {title}"

    async def settle(count: int = 12) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def complete() -> None:
        deadline: float = time.monotonic() + 20
        while extension._tree._tasks:
            assert time.monotonic() < deadline, "Explorer operation timed out"
            await app.next_update_async()
        await settle()

    def widget(title: str, kind: str, identifier: str) -> Any:
        item: Any
        for item in ui_test.find_all(f"{title}//Frame/**/{kind}[*]"):
            if item.widget.identifier == identifier:
                return item
        raise AssertionError(f"Missing {identifier} in {title}")

    async def click(identifier: str, kind: str = "Button", title: str = "Explorer") -> None:
        target: Any = widget(title, kind, identifier)
        # WidgetRef.click() undocks its window; use normal mouse input instead.
        await target.focus()
        await ui_test.emulate_mouse_move_and_click(target.center)
        await settle()

    async def choose_folder(folder: Path) -> None:
        dialog: Any = extension._folder_dialog
        assert dialog is not None
        dialog.set_current_directory(str(folder.parent))
        await settle(30)
        dialog.set_filename(folder.name)
        await settle()
        picker_open: Any = ui_test.find("Open Folder//Frame/**/Button[*].text=='Open'")
        await ui_test.emulate_mouse_move_and_click(picker_open.center)
        await complete()

    def check_row_alignment() -> None:
        node: Any
        depth: int
        for node, depth in extension._tree.rows():
            if node.directory:
                label: Any = widget("Explorer", "Label", f"explorer_name:{node.url}")
                icon: Any = widget("Explorer", "Button", f"explorer_toggle:{node.url}")
                assert abs(icon.center.y - label.center.y) < 1, (
                    f"Misaligned control for {node.name}: icon {icon.position.y}/{icon.size.y}, "
                    f"label {label.position.y}/{label.size.y}"
                )
                assert icon.position.x + icon.size.x < label.position.x

    async def type_name(name: str, confirm_button: bool = False) -> None:
        await ui_test.emulate_char_press(name)
        await settle(3)
        if confirm_button:
            await click("explorer_create_confirm", title="New Folder")
        else:
            await ui_test.emulate_keyboard_press(carb.input.KeyboardInput.ENTER)
        await complete()

    async def capture(name: str) -> None:
        capture_api: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture_api.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(8)

    def child(parent: Any, name: str) -> Any:
        node: Any
        for node in parent.children:
            if node.name == name:
                return node
        raise AssertionError(f"Missing {name}")

    temporary: str
    with TemporaryDirectory(prefix="project # ", dir=output) as temporary:
        project: Path = Path(temporary)
        (project / "Alpha" / "Nested").mkdir(parents=True)
        (project / "beta").mkdir()
        (project / "Alpha" / "Nested" / "inside.txt").write_text("nested", encoding="utf-8")
        (project / "notes.txt").write_text("keep this content", encoding="utf-8")
        (project / "with # and %.txt").write_text("special", encoding="utf-8")
        try:
            await settle(100)
            extension_type: Any = importlib.import_module("explorer.extension").ExplorerExtension
            obj: Any
            for obj in gc.get_objects():
                if isinstance(obj, extension_type):
                    extension = obj
            assert extension is not None
            tree: Any = extension._tree
            assert tree.root is None
            buttons: Any = ui_test.find_all("Explorer//Frame/**/Button[*]")
            assert len(buttons) == 1 and buttons[0].widget.text == "Open Folder"
            explorer_window: Any = ui.Workspace.get_window("Explorer")
            check_layout()
            await capture("initial")

            # Use the real picker button, then cancel without changing the panel.
            await click("explorer_open_folder")
            assert extension._folder_dialog is not None
            await ui_test.emulate_keyboard_press(carb.input.KeyboardInput.ESCAPE)
            await settle()
            assert extension._folder_dialog is None and tree.root is None
            check_layout()
            await click("explorer_open_folder")
            await choose_folder(project)
            root: Any = tree.root
            assert root is not None, tree.error
            assert root.name == project.name
            assert child(root, "Alpha").directory and child(root, "beta").directory
            assert not child(root, "with # and %.txt").directory
            assert root.children[0].name == "Alpha" and root.children[1].name == "beta"
            report["startup_picker_and_sorted_listing"] = True
            check_layout()
            check_row_alignment()
            folders_button: Any = widget("Explorer", "Button", "explorer_folders")
            new_file_button: Any = widget("Explorer", "Button", "explorer_new_file")
            new_folder_button: Any = widget("Explorer", "Button", "explorer_new_folder")
            assert folders_button.center.x < new_file_button.center.x < new_folder_button.center.x
            assert (
                not folders_button.widget.text and not new_file_button.widget.text and not new_folder_button.widget.text
            )

            alpha: Any = child(root, "Alpha")
            await click(f"explorer_toggle:{alpha.url}")
            await complete()
            nested: Any = child(alpha, "Nested")
            await click(f"explorer_toggle:{nested.url}")
            await complete()
            assert child(nested, "inside.txt")
            assert tree.rows()[3][1] == 3
            check_row_alignment()
            await click(f"explorer_toggle:{alpha.url}")
            assert not alpha.expanded
            assert len(tree.rows()) == 5
            await click(f"explorer_name:{root.url}", "Label")
            await click("explorer_new_file")
            await type_name("created.txt")
            check_layout()
            assert (project / "created.txt").is_file()
            assert tree.selected.name == "created.txt"
            await click("explorer_new_folder")
            await type_name("new folder", confirm_button=True)
            assert (project / "new folder").is_dir()
            new_folder: Any = tree.selected
            await click("explorer_new_file")
            await type_name("nested file.txt")
            assert (project / "new folder" / "nested file.txt").is_file()
            assert new_folder.expanded
            report["hierarchy_and_creation_destinations"] = True
            check_layout()
            check_row_alignment()
            await capture("tree")

            # Naming failures preserve existing data and leave the dialog editable.
            await click(f"explorer_name:{root.url}", "Label")
            await click("explorer_new_file")
            await type_name("notes.txt")
            assert extension._create_dialog is not None and extension._create_error.text
            assert (project / "notes.txt").read_text(encoding="utf-8") == "keep this content"
            extension._create_model.set_value("../outside.txt")
            extension._submit_create()
            await complete()
            assert extension._create_dialog is not None and extension._create_error.text
            await ui_test.emulate_keyboard_press(carb.input.KeyboardInput.ESCAPE)
            await settle()
            assert extension._create_dialog is None
            report["duplicate_and_invalid_names"] = True

            (project / "external.txt").write_text("external", encoding="utf-8")
            explorer_window.focus()
            await ui_test.emulate_keyboard_press(carb.input.KeyboardInput.F5)
            await complete()
            external: Any = child(root, "external.txt")
            await click(f"explorer_name:{external.url}", "Label")
            assert tree.selected is external
            context: Any = omni.usd.get_context()
            assert context.get_stage() is None, "Explorer opened a viewport stage"
            await tree.open_folder(str(project / "missing"))
            await settle()
            assert tree.root is root and tree.error

            # The toolbar picker replaces the entire root and clears old selections.
            await click("explorer_folders")
            await choose_folder(project / "Alpha")
            assert tree.root is not None and tree.root is not root and tree.root.name == "Alpha", tree.error
            root = tree.root
            assert tree.selected is root and len(root.children) == 1
            assert child(root, "Nested").directory and not tree.error
            check_layout()
            check_row_alignment()
            report["icon_toolbar_alignment_and_folder_switch"] = True

            root_label: Any = widget("Explorer", "Label", f"explorer_name:{root.url}")
            await root_label.focus()
            await ui_test.emulate_mouse_move_and_click(root_label.center, right_click=True)
            await settle()
            menu_item: Any
            for menu_item in ui.Inspector.get_children(extension._menu):
                if isinstance(menu_item, ui.MenuItem) and menu_item.text == "Close Folder":
                    menu_target: Any = ui_test.WidgetRef(menu_item, "")
                    await ui_test.emulate_mouse_move_and_click(menu_target.center)
                    break
            await settle()
            assert tree.root is None
            assert len(ui_test.find_all("Explorer//Frame/**/Button[*]")) == 1
            check_layout()
            report["refresh_selection_errors_and_reset"] = True
            report["passed"] = True
        except Exception:
            logging.getLogger(__name__).exception("Explorer integration check failed")
            report["passed"] = False
            report["error"] = traceback.format_exc()
            await capture("failure")
            await settle(60)
        finally:
            (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print("EXPLORER " + json.dumps(report), flush=True)
    app.post_uncancellable_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
