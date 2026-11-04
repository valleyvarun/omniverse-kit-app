"""Orthonormal pipe rings for paths that leave a single drawing plane."""

from __future__ import annotations

import numpy as np

from tool_box.tools.drawing.curves.mesh import FloatArray, PipeMesh, Ring


class SurfacePipeMesh(PipeMesh):
    def _ring(self, center: FloatArray, tangent: FloatArray, radius: float, slope: float = 0.0) -> Ring:
        direction: FloatArray = tangent / max(float(np.linalg.norm(tangent)), 1e-12)
        normal: FloatArray = self._normal - direction * float(np.dot(self._normal, direction))
        if float(np.linalg.norm(normal)) < 1e-6:
            reference: FloatArray = np.eye(3, dtype=np.float64)[int(np.argmin(np.abs(direction)))]
            normal = reference - direction * float(np.dot(reference, direction))
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
        side: FloatArray = np.cross(direction, normal).astype(np.float64, copy=False)
        radial: FloatArray = self._profile[:, :1] * normal + self._profile[:, 1:] * side
        normals: FloatArray = radial / np.linalg.norm(radial, axis=1, keepdims=True) - slope * direction
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
        # Parallel transport avoids flips when a surface path turns through the original normal.
        self._normal = normal
        return center + radius * radial, normals
