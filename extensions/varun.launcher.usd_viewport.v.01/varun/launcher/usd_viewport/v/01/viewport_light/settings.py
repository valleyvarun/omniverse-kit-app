"""Names shared by the Python UI and the native viewport policy."""

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

ROOT: str = "/exts/varun.launcher.usd_viewport.v.01/viewportLight"
THEME: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme"
STORM: str = "HdStormRendererPlugin"
LABEL: str = "Viewport Light"

# grid label unit chosen from the viewport's Units button, and each unit's size in metres
UNITS: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/units"
UNIT_METERS: dict[str, float] = {"m": 1.0, "mm": 0.001}
DEFAULT_UNIT: str = "m"

# Yellow ruler numbers; the axes use the original RGB colors.
RULER: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/ruler/visible"
RULER_COLOR: tuple[float, float, float, float] = (1.0, 0.85, 0.1, 1.0)
