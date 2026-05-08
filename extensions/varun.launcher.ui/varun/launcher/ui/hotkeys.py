import logging
from typing import Any, Callable, cast

from .tools.transform_tools import OP_MOVE, OP_ROTATE, OP_SCALE, set_transform_op
from .windows.top_window import TopWindow


LOGGER = logging.getLogger(__name__)


# Registers and tears down the launcher's custom hotkeys and actions.
class HotkeyManager:
    HOTKEY_EXT_ID = "varun.launcher.ui"
    FOCUS_COMMAND_ACTION_ID = "focus_command"
    MOVE_ACTION_ID = "transform_move"
    ROTATE_ACTION_ID = "transform_rotate"
    SCALE_ACTION_ID = "transform_scale"

    def __init__(self, top_window_provider: Callable[[], TopWindow | None]) -> None:
        # Provider lets the manager fetch the current top window lazily, since the
        # launcher creates/destroys windows independently of hotkey registration.
        self._top_window_provider = top_window_provider

    # Register the launcher's custom hotkeys.
    def register(self) -> None:
        try:
            import omni.kit.actions.core as kit_actions
            import omni.kit.hotkeys.core as kit_hotkeys
        except ImportError:
            LOGGER.warning("omni.kit.actions.core / omni.kit.hotkeys.core not available; skipping hotkeys")
            return

        action_registry = cast(Any, kit_actions).get_action_registry()
        hotkey_registry = cast(Any, kit_hotkeys).get_hotkey_registry()

        # Action: focus the command bar.
        action_registry.register_action(
            self.HOTKEY_EXT_ID,
            self.FOCUS_COMMAND_ACTION_ID,
            self._focus_command_action,
            display_name="Focus Command",
            description="Focus the launcher command line.",
            tag="Launcher",
        )
        # Hotkey: C -> focus command bar.
        hotkey_registry.register_hotkey(
            hotkey_ext_id=self.HOTKEY_EXT_ID,
            key="C",
            action_ext_id=self.HOTKEY_EXT_ID,
            action_id=self.FOCUS_COMMAND_ACTION_ID,
            filter=None,
        )

        # Actions + hotkeys for the standard Move / Rotate / Scale viewport tools.
        # Some other extensions (e.g. omni.kit.manipulator.tool.snap registers S for snap toggle)
        # already claim these keys as global hotkeys; the registry rejects duplicates with a
        # warning and silently fails. Deregister any conflicts first so M/R/S go to our actions.
        def _make_op_callback(op_name: str) -> Callable[..., None]:
            def _callback(*_args: object, **_kwargs: object) -> None:
                set_transform_op(op_name)
            return _callback

        for action_id, key, op, label in (
            (self.MOVE_ACTION_ID, "M", OP_MOVE, "Move"),
            (self.ROTATE_ACTION_ID, "R", OP_ROTATE, "Rotate"),
            (self.SCALE_ACTION_ID, "S", OP_SCALE, "Scale"),
        ):
            # Wipe any pre-existing hotkey bound to this single key so registration succeeds.
            try:
                for existing in list(hotkey_registry.get_all_hotkeys_for_key(key)):
                    hotkey_registry.deregister_hotkey(existing)
            except Exception as exc:
                LOGGER.warning("Could not clear existing hotkey for %r: %s", key, exc)

            action_registry.register_action(
                self.HOTKEY_EXT_ID,
                action_id,
                _make_op_callback(op),
                display_name=label,
                description=f"Activate the {label} transform manipulator.",
                tag="Launcher",
            )
            registered = hotkey_registry.register_hotkey(
                hotkey_ext_id=self.HOTKEY_EXT_ID,
                key=key,
                action_ext_id=self.HOTKEY_EXT_ID,
                action_id=action_id,
                filter=None,
            )
            if registered is None:
                LOGGER.warning("Failed to register %r hotkey for %s", key, label)

    # Unregister all hotkeys and actions for this extension.
    def deregister(self) -> None:
        try:
            import omni.kit.actions.core as kit_actions
            import omni.kit.hotkeys.core as kit_hotkeys
        except ImportError:
            return

        try:
            cast(Any, kit_hotkeys).get_hotkey_registry().deregister_all_hotkeys_for_extension(self.HOTKEY_EXT_ID)
        except Exception:
            pass
        try:
            cast(Any, kit_actions).get_action_registry().deregister_all_actions_for_extension(self.HOTKEY_EXT_ID)
        except Exception:
            pass

    # Callback for the C hotkey: focus the top window's command field.
    def _focus_command_action(self) -> None:
        top_window = self._top_window_provider()
        if top_window is not None:
            top_window.focus_command_field()
