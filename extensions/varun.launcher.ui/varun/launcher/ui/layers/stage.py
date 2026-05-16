from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.ui as ui

from ..tools.selection.selection import LockManager


LOGGER = logging.getLogger(__name__)

# Stage-window column header label.
_COLUMN_NAME = "Lock"

# Persistent setting that omni.kit.window.stage reads to decide which columns
# to show (pipe-separated list).
_STAGE_COLUMNS_SETTING = "/persistent/exts/omni.kit.window.stage/columns"

# SVG icons (resolve to <python_package>/logos/<name>.svg).
_ICONS_DIR = Path(__file__).resolve().parent.parent / "logos"
_ICON_LOCKED = str(_ICONS_DIR / "lock_locked.svg")
_ICON_UNLOCKED = str(_ICONS_DIR / "lock_unlocked.svg")


# Adds a "Lock" column to the Stage window. All lock state and enforcement
# lives in selection.py LockManager - this class only owns the UI.
class StageLockColumn:
    def __init__(self, lock_manager: LockManager) -> None:
        self._lock_manager = lock_manager
        self._column_sub: Any = None

    def apply(self) -> None:
        self._register_column()
        self._ensure_column_visible()

    def destroy(self) -> None:
        self._column_sub = None

    def _register_column(self) -> None:
        try:
            from omni.kit.widget.stage import StageColumnDelegateRegistry
        except Exception as exc:
            LOGGER.warning("StageLockColumn: stage widget API unavailable: %s", exc)
            return
        manager = self._lock_manager
        def _factory() -> Any:
            return _LockColumnDelegate(manager)
        try:
            self._column_sub = cast(Any, StageColumnDelegateRegistry)().register_column_delegate(
                _COLUMN_NAME, _factory
            )
        except Exception as exc:
            LOGGER.warning("StageLockColumn: failed to register lock column: %s", exc)

    def _ensure_column_visible(self) -> None:
        try:
            settings = cast(Any, carb.settings.get_settings())
            current = settings.get(_STAGE_COLUMNS_SETTING)
            if current is None:
                names = ["Visibility", "Type"]
            else:
                names = [n for n in str(current).split("|") if n]
            if _COLUMN_NAME not in names:
                names.append(_COLUMN_NAME)
                settings.set(_STAGE_COLUMNS_SETTING, "|".join(names))
        except Exception as exc:
            LOGGER.warning("StageLockColumn: failed to update column setting: %s", exc)
        try:
            import gc
            from omni.kit.widget.stage.column_menu import ColumnMenuModel  # type: ignore
            for obj in gc.get_objects():
                if isinstance(obj, ColumnMenuModel):
                    self._enable_in_model(obj)
        except Exception:
            pass

    def _enable_in_model(self, model: Any) -> None:
        try:
            for child in getattr(model, "_children", []) or []:
                if child.name_model.as_string == _COLUMN_NAME:
                    if not child.checked_model.as_bool:
                        child.checked_model.set_value(True)
                    return
        except Exception:
            pass


def _column_delegate_base() -> type:
    try:
        from omni.kit.widget.stage import AbstractStageColumnDelegate
        return cast(type, AbstractStageColumnDelegate)
    except Exception:
        return object


_BaseColumnDelegate = _column_delegate_base()


class _LockColumnDelegate(_BaseColumnDelegate):  # type: ignore[misc, valid-type]
    def __init__(self, manager: LockManager) -> None:
        try:
            cast(Any, super()).__init__()
        except Exception:
            pass
        self._manager = manager

    def destroy(self) -> None:
        pass

    @property
    def initial_width(self) -> Any:
        return ui.Pixel(28)

    @property
    def minimum_width(self) -> Any:
        return ui.Pixel(20)

    @property
    def sortable(self) -> bool:
        return False

    @property
    def order(self) -> int:
        return 50

    @property
    def resizable(self) -> bool:
        return True

    def build_header(self, **kwargs: Any) -> None:
        with ui.HStack():
            ui.Spacer()
            ui.Image(
                _ICON_LOCKED,
                width=16,
                height=16,
                fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
            )
            ui.Spacer()

    async def build_widget(self, item: Any, **kwargs: Any) -> None:
        if item is None or item.stage is None:
            return
        prim = item.stage.GetPrimAtPath(item.path)
        if not prim or not prim.IsValid():
            return
        manager = self._manager
        path = item.path
        with ui.HStack(height=20):
            ui.Spacer()
            image = ui.Image(
                _ICON_LOCKED if manager.is_locked(prim) else _ICON_UNLOCKED,
                width=16,
                height=16,
                fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
            )
            ui.Spacer()

        # Refresh this row's icon whenever any lock state changes so
        # ancestor toggles propagate visually to inherited descendants.
        def _refresh() -> None:
            try:
                stage = item.stage
                p = stage.GetPrimAtPath(path)
                if not p or not p.IsValid():
                    return
                image.source_url = _ICON_LOCKED if manager.is_locked(p) else _ICON_UNLOCKED
            except Exception:
                pass

        manager.add_listener(_refresh)

        # Drop the listener when the underlying ui.Image is destroyed.
        def _on_destroy(*_args: Any) -> None:
            manager.remove_listener(_refresh)
        try:
            cast(Any, image).set_destroy_fn(_on_destroy)
        except Exception:
            pass

        def _on_click(*_: Any) -> None:
            stage = item.stage
            p = stage.GetPrimAtPath(path)
            if not p or not p.IsValid():
                return
            # Toggle this prim's OWN lock state. Inherited locks from
            # ancestors are not affected here, so unlocking a parent never
            # silently unlocks a child that was explicitly locked.
            new_state = not manager.is_locked_self(p)
            manager.set_locked(p, new_state)

        image.set_mouse_pressed_fn(_on_click)
