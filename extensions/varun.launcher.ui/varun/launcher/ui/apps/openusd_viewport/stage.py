"""USD stage lifecycle helpers for the openusd_viewport package.

Pure functions: open a fresh stage with the default light rig, clear a
context's prim selection, close a stage to free its USD + Hydra memory.
"""

import logging
from typing import Any, Callable, cast

import omni.kit.app
import omni.kit.stage_templates as stage_templates
import omni.usd

from .tab import DEFAULT_LIGHT_RIG


LOGGER = logging.getLogger(__name__)


async def initialize_viewport_stage(
    context_name: str,
    is_current: Callable[[], bool],
) -> None:
    """Open a fresh empty stage in ``context_name`` and apply the light rig.

    No-op if the context already has a stage open (re-focus case). The
    ``is_current`` predicate is checked after every ``await`` so a quick
    tab-switch can abort the coroutine before it touches USD on behalf
    of a stale generation.
    """
    if not context_name:
        return
    app = cast(Any, omni.kit.app.get_app())

    for _ in range(5):
        if not is_current():
            return
        try:
            await app.next_update_async()
        except Exception:
            return
    if not is_current():
        return

    try:
        ctx = cast(Any, omni.usd).get_context(context_name)
    except Exception:
        return
    if ctx is None:
        return
    try:
        if ctx.get_stage() is not None:
            # Stage was opened on a previous focus; nothing to do.
            return
    except Exception:
        pass

    opened = False
    try:
        cast(Any, stage_templates).new_stage(template=None, usd_context=context_name)
        opened = True
    except TypeError:
        try:
            ctx.new_stage()
            opened = True
        except Exception:
            pass
    except Exception:
        try:
            ctx.new_stage()
            opened = True
        except Exception:
            pass
    if not opened:
        return

    for _ in range(5):
        if not is_current():
            return
        try:
            await app.next_update_async()
        except Exception:
            return
    if not is_current():
        return

    # Re-fetch ctx after the awaits -- on hot-reload the original
    # reference can outlive the underlying C++ UsdContext.
    try:
        ctx = cast(Any, omni.usd).get_context(context_name)
    except Exception:
        return
    if ctx is None:
        return
    try:
        if ctx.get_stage() is None:
            return
    except Exception:
        return

    try:
        import omni.kit.commands as kit_commands
    except ImportError:
        return
    try:
        cast(Any, kit_commands).execute(
            "SetLightingMenuModeCommand",
            lighting_mode=DEFAULT_LIGHT_RIG,
            usd_context_name=context_name,
        )
    except Exception:
        try:
            cast(Any, kit_commands).execute(
                "SetLightingMenuMode",
                lighting_mode=DEFAULT_LIGHT_RIG,
                usd_context_name=context_name,
            )
        except Exception as exc:
            LOGGER.warning(
                "Could not apply default light rig %r to context %r: %s",
                DEFAULT_LIGHT_RIG,
                context_name,
                exc,
            )


def clear_selection(ctx_name: str) -> None:
    """Drop all selected prims in ``ctx_name``.

    Called right before destroying a ViewportWindow so manipulators
    unregister cleanly. Without this, destroying a multi-context
    ViewportWindow has been observed to segfault inside
    ``manipulator.selector._refresh``.
    """
    if not ctx_name:
        return
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
        if ctx is None:
            return
        sel = ctx.get_selection()
        if sel is not None:
            sel.clear_selected_prim_paths()
    except Exception:
        pass


def release_stage(ctx_name: str) -> None:
    """Release the USD stage held by a named context.

    The UsdContext shell stays alive (``ManipulatorSelector`` caches a
    strong pointer per name and segfaults if it dangles), but closing
    its stage drops the prim/geometry/texture memory.
    """
    if not ctx_name:
        return
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
    except Exception:
        return
    if ctx is None:
        return
    try:
        ctx.set_pending_edit(False)
    except Exception:
        pass

    def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    try:
        ctx.close_stage_with_callback(_noop)
    except Exception as exc:
        LOGGER.warning("close_stage_with_callback failed for %r: %s", ctx_name, exc)
