"""Save / Don't Save / Cancel dirty-close prompt for viewport tabs."""

import asyncio
import logging
from typing import Any, Callable, cast


LOGGER = logging.getLogger(__name__)


def show_save_prompt(
    title: str,
    ctx: Any,
    do_close: Callable[[], Any],
    on_confirmed: Callable[[], None] | None,
    on_cancelled: Callable[[], None] | None,
) -> None:
    """Post the 3-button Save / Don't Save / Cancel prompt.

    * Save       -> save (file picker if untitled) then close
    * Don't Save -> close, discarding edits
    * Cancel     -> leave the viewport open
    """
    try:
        from omni.kit.widget.prompt import PromptButtonInfo, PromptManager
    except Exception as exc:  # pragma: no cover - prompt ext should be loaded
        LOGGER.warning("PromptManager unavailable, closing without prompt: %s", exc)
        do_close()
        if on_confirmed is not None:
            on_confirmed()
        return

    def _do_close() -> None:
        do_close()
        if on_confirmed is not None:
            on_confirmed()

    def _on_save() -> None:
        save_then_close(title, ctx, do_close, on_confirmed, on_cancelled)

    def _on_dont_save() -> None:
        _do_close()

    def _on_cancel() -> None:
        if on_cancelled is not None:
            on_cancelled()

    cast(Any, PromptManager).post_simple_prompt(
        title=f"Close '{title}'",
        message=f"'{title}' has unsaved changes. Do you want to save them?",
        ok_button_info=PromptButtonInfo("Save", _on_save),
        middle_button_info=PromptButtonInfo("Don't Save", _on_dont_save),
        cancel_button_info=PromptButtonInfo("Cancel", _on_cancel),
        modal=True,
    )


def save_then_close(
    title: str,
    ctx: Any,
    do_close: Callable[[], Any],
    on_confirmed: Callable[[], None] | None,
    on_cancelled: Callable[[], None] | None,
) -> None:
    """Save the context's stage, then close the tab on success."""

    def _on_done(success: bool) -> None:
        LOGGER.info("save_then_close(%r) -> success=%s", title, success)
        if success:
            do_close()
            if on_confirmed is not None:
                on_confirmed()
        else:
            if on_cancelled is not None:
                on_cancelled()

    is_new = True
    try:
        is_new = bool(ctx.is_new_stage())
    except Exception:
        pass

    # Backed by a file -> save in place via the async wrapper so we get
    # a proper (result, err, layers) tuple back.
    if not is_new:
        async def _save_in_place() -> None:
            try:
                result_tuple = await ctx.save_stage_async()
                result = bool(result_tuple[0]) if result_tuple else False
                err = result_tuple[1] if result_tuple and len(result_tuple) > 1 else ""
                if not result:
                    LOGGER.warning("save_stage_async failed for %r: %s", title, err)
                _on_done(result)
            except Exception as exc:
                LOGGER.exception("save_stage_async raised for %r: %s", title, exc)
                _on_done(False)

        asyncio.ensure_future(_save_in_place())
        return

    # Brand-new (untitled) stage -> show a file picker, then save_as.
    exporter: Any = None
    try:
        from omni.kit.window.file_exporter import get_file_exporter
        exporter = get_file_exporter()
    except Exception as exc:
        LOGGER.warning("file_exporter unavailable for %r: %s", title, exc)
        _on_done(False)
        return
    if exporter is None:
        _on_done(False)
        return

    def _on_picked(
        filename: str,
        dirname: str,
        extension: str = "",
        selections: list[Any] | None = None,
    ) -> None:
        del selections
        LOGGER.info(
            "file picker for %r -> filename=%r dirname=%r ext=%r",
            title, filename, dirname, extension,
        )
        if not filename:
            _on_done(False)
            return

        # Build a proper URL the same way omni.kit.window.file does.
        import omni.client as _client  # type: ignore
        _dir = dirname
        if _dir and not _dir.endswith("/"):
            _dir = _dir + "/"
        leaf = f"{filename}{extension}" if extension else filename
        url = cast(Any, _client).make_absolute_url_if_possible(_dir, leaf)
        LOGGER.info("save_as resolved url=%r", url)

        async def _do_save_as() -> None:
            try:
                result_tuple = await ctx.save_as_stage_async(url)
                result = bool(result_tuple[0]) if result_tuple else False
                err = result_tuple[1] if result_tuple and len(result_tuple) > 1 else ""
                if not result:
                    LOGGER.warning("save_as_stage_async failed for %r: %s", title, err)
                _on_done(result)
            except Exception as exc:
                LOGGER.exception("save_as_stage_async raised for %r: %s", title, exc)
                _on_done(False)

        asyncio.ensure_future(_do_save_as())

    def _on_picker_cancel(*_args: Any) -> None:
        LOGGER.info("file picker cancelled for %r", title)
        _on_done(False)

    try:
        exporter.show_window(
            title=f"Save '{title}' As",
            export_button_label="Save",
            export_handler=_on_picked,
            click_cancel_handler=_on_picker_cancel,
            file_extension_types=[
                (".usd", "USD"),
                (".usda", "USD ASCII"),
                (".usdc", "USD Binary"),
            ],
            should_validate=True,
        )
    except Exception as exc:
        LOGGER.warning("file_exporter.show_window failed for %r: %s", title, exc)
        _on_done(False)
