"""Drawing defaults shared by the brush, cursor, and stroke tools."""

from tool_box.core.resources import ICONS_DIR as ICONS_DIR

# Viewport controls temporarily disabled while drawing.
VIEWPORT_CONTEXT_MENU_SETTING = "/exts/omni.kit.window.viewport/showContextMenu"
UNSET = object()

# Stroke color, spacing, and frame update interval.
SPHERE_COLOR = (0.0, 0.0, 0.0)
EMIT_INTERVAL = 0.016
SAMPLE_MIN_DISTANCE = 0.1

# Temporary strokes are consolidated on mouse release.
DRAW_ROOT = "/World/ThreeDDraw"
LIVE_STROKE_PATH = f"{DRAW_ROOT}/_LiveStroke"
CHUNK_SPHERE_LIMIT = 20000
INITIAL_STROKE_CAPACITY = 4096

# Register brush overlays separately from other extensions.
CURSOR_FACTORY_ID = "tool_box.cursor_3d"
RING_COLOR = (1.0, 0.85, 0.2, 1.0)
