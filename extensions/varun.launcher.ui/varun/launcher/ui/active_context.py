"""Active-viewport USD context helpers.

Every viewport tab (Home / Viewport / Viewport 1 / Viewport 2 / ...) can
optionally bind to its own `UsdContext`. When tools and panels reach for
`omni.usd.get_context()` they always get the *global* (`""`) context,
which only matches the default `Viewport` tab. The helpers here return
the context attached to whichever viewport the user is currently
interacting with, so tools act on the stage they're looking at.

Usage:

    from ...active_context import get_active_usd_context, get_active_stage

    ctx = get_active_usd_context()
    stage = get_active_stage()

Tools that already have a `viewport_api` in scope (e.g. tools registered
through `omni.kit.viewport.registry`) should pass it through:

    ctx = get_active_usd_context(self._viewport_api)
    stage = get_active_stage(self._viewport_api)

That guarantees the call resolves to the viewport the input event came
from, even if the user has since clicked into a different tab.
"""

from typing import Any, cast

import omni.usd


# Name of the dedicated, stage-less UsdContext used when the Home tab is
# the active tab. Home is not a viewport -- it has no stage -- so any
# Kit-owned panel that follows the active context (Stage, Layer, etc.)
# binds to this empty context and renders empty.
HOME_CONTEXT_NAME: str = "home"


def ensure_home_context() -> Any:
    """Create (idempotently) the dedicated empty UsdContext for the Home tab.

    Returns the resulting context, or `None` if creation failed.
    """
    try:
        ctx = omni.usd.get_context(HOME_CONTEXT_NAME)
    except Exception:
        ctx = None
    if ctx is not None:
        return ctx
    try:
        cast(Any, omni.usd).create_context(HOME_CONTEXT_NAME)
    except Exception:
        return None
    try:
        return omni.usd.get_context(HOME_CONTEXT_NAME)
    except Exception:
        return None


# Explicitly-tracked active viewport context name. Updated from
# `OpenUsdViewportManager` whenever a viewport tab becomes the selected
# tab in its dock group. This is the *authoritative* source of truth --
# Kit's `get_active_viewport()` relies on window focus, which doesn't
# move on every viewport click (and never moves on right-clicks), so it
# is unreliable for routing tool actions.
#
# Lowercase deliberately: pylance treats UPPER_CASE module-level names as
# Final and refuses reassignment, but this *is* mutated by
# `set_active_context_name`.
_active_context_name: str = ""

# Listeners fired whenever the active context name changes (i.e. on
# viewport-tab switches). Registries that cache per-stage state subscribe
# here to re-sync to the new active stage; the C_Layers panel subscribes
# here to rebuild itself.
_ACTIVE_CONTEXT_LISTENERS: list[Any] = []


def add_active_context_listener(callback: Any) -> None:
    """Register a no-arg callback fired on every active-tab change."""
    if callback not in _ACTIVE_CONTEXT_LISTENERS:
        _ACTIVE_CONTEXT_LISTENERS.append(callback)


def remove_active_context_listener(callback: Any) -> None:
    """Unregister a previously-added active-context listener."""
    try:
        _ACTIVE_CONTEXT_LISTENERS.remove(callback)
    except ValueError:
        pass


def set_active_context_name(name: str) -> None:
    """Record the UsdContext name of whichever viewport tab is now selected.

    Pass `""` for the default global-context viewport tab. Fires every
    registered active-context listener when the name actually changes.
    """
    global _active_context_name
    new_name = str(name or "")
    if new_name == _active_context_name:
        return
    _active_context_name = new_name
    for cb in list(_ACTIVE_CONTEXT_LISTENERS):
        try:
            cb()
        except Exception:
            pass


def get_active_context_name() -> str:
    """Return the UsdContext name of the currently-selected viewport tab."""
    return _active_context_name


def _context_from_name(name: str) -> Any | None:
    """Return the UsdContext for the given name, or the global one."""
    try:
        ctx = omni.usd.get_context(name)
    except Exception:
        ctx = None
    if ctx is None and name:
        try:
            ctx = omni.usd.get_context()
        except Exception:
            ctx = None
    return ctx


def get_active_usd_context(viewport_api: Any = None) -> Any:
    """Return the UsdContext for the currently-active viewport.

    Preference order:
        1. The context attached to the supplied `viewport_api`, if any.
        2. The context attached to whichever viewport Kit currently
           reports as active.
        3. The global context (same as `omni.usd.get_context()`).
    """
    if viewport_api is not None:
        name = getattr(viewport_api, "usd_context_name", "")
        ctx = _context_from_name(str(name))
        if ctx is not None:
            return ctx

    # Prefer our explicitly-tracked active tab (set by the viewport
    # manager's dock-changed callback) -- it's authoritative and not
    # subject to OS window-focus quirks.
    if _active_context_name:
        ctx = _context_from_name(_active_context_name)
        if ctx is not None:
            return ctx

    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore[import-not-found]

        vp = cast(Any, get_active_viewport())
        if vp is not None:
            name = getattr(vp, "usd_context_name", "")
            ctx = _context_from_name(str(name))
            if ctx is not None:
                return ctx
    except Exception:
        pass

    return omni.usd.get_context()


def get_active_stage(viewport_api: Any = None) -> Any | None:
    """Return the USD `Stage` for the currently-active viewport."""
    ctx = get_active_usd_context(viewport_api)
    if ctx is None:
        return None
    try:
        return ctx.get_stage()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Global `omni.usd.get_context()` shim
# ---------------------------------------------------------------------------
# Kit-owned panels (right-click Create menu, File menu, Stage tree, Property
# panel, etc.) call `omni.usd.get_context()` with no arguments and therefore
# always target the global ("") context -- which means they only ever act on
# the default `Viewport` tab. Forking each of those extensions to make them
# tab-aware would be hostile to Kit upgrades; instead we monkey-patch
# `omni.usd.get_context` itself so that, whenever it's invoked with no name
# (or with the empty string AND a named viewport is currently active), the
# active viewport's named context is returned in place of the global one.
#
# Explicitly-named lookups (e.g. `omni.usd.get_context("preview")`) are NOT
# rerouted -- callers that ask for a specific context get exactly that.
#
# Install once at extension startup; the original is restored on shutdown.

# Lowercase deliberately: pylance treats UPPER_CASE module-level names as
# Final, but this is rebound by install/uninstall.
_original_get_context: Any = None


def _resolve_active_context_name() -> str:
    """Return the active viewport's `usd_context_name`, or '' if none."""
    # The explicitly-tracked tab wins -- see `set_active_context_name`.
    if _active_context_name:
        return _active_context_name
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore[import-not-found]

        vp = cast(Any, get_active_viewport())
        if vp is not None:
            name = getattr(vp, "usd_context_name", "") or ""
            return str(name)
    except Exception:
        pass
    return ""


def install_global_context_shim() -> None:
    """Patch `omni.usd.get_context` so unnamed lookups follow the active tab.

    Idempotent. Safe to call after Kit and its dependent extensions have
    finished their own startup wiring (so initial subscriptions are still
    bound to the global context the way Kit expects).
    """
    global _original_get_context
    if _original_get_context is not None:
        return
    original = omni.usd.get_context
    _original_get_context = original

    def _patched_get_context(name: str = "") -> Any:
        # Empty name -> follow active viewport (only if it has a non-empty
        # named context, otherwise fall through to the original global).
        if not name:
            active_name = _resolve_active_context_name()
            if active_name:
                try:
                    ctx = cast(Any, original(active_name))
                    if ctx is not None:
                        return ctx
                except Exception:
                    pass
        return original(name)

    try:
        omni.usd.get_context = _patched_get_context  # type: ignore[assignment]
    except Exception:
        _original_get_context = None


def uninstall_global_context_shim() -> None:
    """Restore the original `omni.usd.get_context`."""
    global _original_get_context
    if _original_get_context is None:
        return
    try:
        omni.usd.get_context = _original_get_context  # type: ignore[assignment]
    except Exception:
        pass
    _original_get_context = None
