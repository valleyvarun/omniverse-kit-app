"""Stage helpers for the openusd_viewport package.

In-memory architecture: every tab owns a ``Usd.Stage`` registered in
``UsdUtils.StageCache.Get()``. Tab switches attach a different
stage_id to the shared ``UsdContext`` via ``attach_stage_with_callback``.
No disk I/O is involved, so the per-Kit-build crate/usda
``customLayerData`` serialization bugs (``members = []`` /
``Attempted to unpack unsupported type enum value 0``) do not apply.
"""

import asyncio
import logging
from typing import Any, cast

import carb.settings
import omni.kit.app
import omni.usd
from pxr import Usd, UsdUtils

from .tab import DEFAULT_LIGHT_RIG


LOGGER = logging.getLogger(__name__)


# Per-stage carb key the lighting menubar uses to remember which rig /
# mode is applied. Keyed by the stage's StageCache long-int id (the same
# id we store in ``Tab.stage_id``). An empty value means "Stage Lights".
_LIGHTING_MODE_KEY = "/exts/omni.kit.viewport.menubar.lighting/lightingMode/{stage_id}"


def create_empty_stage() -> int | None:
    """Create a fresh in-memory ``Usd.Stage`` and return its cache id.

    The stage is kept alive by ``UsdUtils.StageCache.Get()``; callers
    must eventually release it via :func:`release_stage`.
    """
    try:
        stage = Usd.Stage.CreateInMemory()
    except Exception as exc:
        LOGGER.warning("Usd.Stage.CreateInMemory failed: %s", exc)
        return None
    try:
        return UsdUtils.StageCache.Get().Insert(stage).ToLongInt()
    except Exception as exc:
        LOGGER.warning("StageCache.Insert failed: %s", exc)
        return None


def release_stage(stage_id: int | None) -> None:
    """Erase ``stage_id`` from the global cache. Safe on ``None``."""
    if stage_id is None:
        return
    try:
        cache = UsdUtils.StageCache.Get()
        cache.Erase(Usd.StageCache.Id.FromLongInt(stage_id))
    except Exception as exc:
        LOGGER.warning("StageCache.Erase(%s) failed: %s", stage_id, exc)


def refresh_stage_id(ctx_name: str, current_id: int | None) -> int | None:
    """Re-cache the context's current stage and return its (possibly new) id.

    Used to follow Save As / Open File flows where Kit may swap the
    stage object behind our back. If the context still has the same
    stage object, ``StageCache.Insert`` returns the existing id (it's
    idempotent on duplicate inserts); if it's a different object the
    old id is erased and the new one returned.
    """
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
    except Exception:
        return current_id
    if ctx is None:
        return current_id
    try:
        stage = ctx.get_stage()
    except Exception:
        stage = None
    if stage is None:
        return current_id

    try:
        new_id = UsdUtils.StageCache.Get().Insert(stage).ToLongInt()
    except Exception as exc:
        LOGGER.warning("StageCache.Insert on refresh failed: %s", exc)
        return current_id

    if current_id is not None and current_id != new_id:
        release_stage(current_id)
    return new_id


async def attach_stage(ctx_name: str, stage_id: int) -> bool:
    """Attach ``stage_id`` to the named UsdContext. Returns True on success."""
    if not ctx_name or stage_id is None:
        return False
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
    except Exception:
        return False
    if ctx is None:
        return False

    try:
        loop = asyncio.get_event_loop()
    except Exception:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    fut: "asyncio.Future[bool]" = loop.create_future()

    def _on_done(success: bool, err: str = "") -> None:
        if fut.done():
            return
        if not success:
            LOGGER.warning("attach_stage(%s) failed: %s", stage_id, err)
        try:
            fut.set_result(bool(success))
        except Exception:
            pass

    try:
        ctx.attach_stage_with_callback(int(stage_id), _on_done)
    except Exception as exc:
        LOGGER.warning("attach_stage_with_callback raised: %s", exc)
        return False

    try:
        return await asyncio.wait_for(fut, timeout=10.0)
    except asyncio.TimeoutError:
        LOGGER.warning("attach_stage(%s) timed out", stage_id)
        return False


def get_lighting_mode(ctx_name: str) -> str | None:
    """Return the lighting mode currently applied to ``ctx_name``'s stage.

    Reads the per-stage carb key the lighting menubar maintains. Returns
    ``""`` for Stage Lights, a rig name (e.g. ``"Grey Studio"``) /
    ``"camera"`` / ``"off"`` otherwise, or ``None`` if it can't be read
    (so callers can tell "unknown" apart from "Stage Lights").
    """
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
        stage = ctx.get_stage() if ctx is not None else None
        if stage is None:
            return None
        stage_id = UsdUtils.StageCache.Get().GetId(stage).ToLongInt()
        key = _LIGHTING_MODE_KEY.format(stage_id=stage_id)
        value = cast(Any, carb.settings.get_settings()).get(key)
        return "" if value is None else str(value)
    except Exception:
        return None


async def apply_lighting_mode(ctx_name: str, mode: str) -> None:
    """Run ``SetLightingMenuModeCommand`` with ``mode`` on the attached stage.

    ``mode`` is the lighting menubar mode string: ``""`` for Stage
    Lights, a rig name like ``"Grey Studio"``, or ``"camera"`` / ``"off"``.

    ``attach_stage_with_callback`` reports success the moment Kit binds
    the stage object, but ``UsdContext.get_stage()`` can still be ``None``
    for a few subsequent frames while the renderer / hydra side catches
    up. Calling the lighting command in that window logs
    ``UsdContext had no stage`` and silently leaves the stage unlit, so
    we poll for a real stage first.

    Best effort: if the stage never becomes available the call is
    skipped rather than raising.
    """
    try:
        import omni.kit.commands as kit_commands
    except ImportError:
        return

    app = cast(Any, omni.kit.app.get_app())
    ctx: Any = None
    try:
        ctx = cast(Any, omni.usd).get_context(ctx_name)
    except Exception:
        ctx = None
    if ctx is None:
        return

    for _ in range(30):
        try:
            stage = ctx.get_stage()
        except Exception:
            stage = None
        if stage is not None:
            break
        try:
            await app.next_update_async()
        except Exception:
            return
    else:
        LOGGER.warning(
            "apply_lighting_mode: stage never became available on %r",
            ctx_name,
        )
        return

    try:
        cast(Any, kit_commands).execute(
            "SetLightingMenuModeCommand",
            lighting_mode=mode,
            usd_context_name=ctx_name,
        )
    except Exception as exc:
        LOGGER.warning(
            "SetLightingMenuModeCommand %r on %r failed: %s",
            mode, ctx_name, exc,
        )


async def apply_default_light_rig(ctx_name: str) -> None:
    """Apply the default (``DEFAULT_LIGHT_RIG``) lighting mode. Best effort."""
    await apply_lighting_mode(ctx_name, DEFAULT_LIGHT_RIG)


def clear_selection(ctx_name: str) -> None:
    """Drop selected prims in ``ctx_name``. Silent on failure."""
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
