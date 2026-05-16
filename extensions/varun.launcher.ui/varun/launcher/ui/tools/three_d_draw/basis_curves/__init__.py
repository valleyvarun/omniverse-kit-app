"""Experimental BasisCurves variant of the 3D Draw tool.

Authors one `UsdGeom.BasisCurves` prim per stroke instead of N
`UsdGeom.Points`. Lives entirely under `/World/ThreeDDrawCurves/` and
does NOT touch any of the production tool's mutable state. Intended
as a perf / look comparison against the production tool.
"""

from .three_d_draw_curves import ThreeDDrawCurvesTool

__all__ = ["ThreeDDrawCurvesTool"]
