"""Transient studio lights: never author into a user's root or edit-target layer."""

# Python & library imports
import asyncio
from typing import Any, cast

# Omniverse & USD imports
import omni.kit.app
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdLux

# ----------------------------------------------------------------------
# 1. STUDIO RIG
# ----------------------------------------------------------------------


class StudioRig:
    # Track the temporary lighting layer and refresh subscriptions.
    def __init__(self):
        self._stage = None
        self._layer = None
        self._rig_stage = None
        self._root_path = None
        self._rotation = None
        self._hidden_paths = set()
        self._notice = None
        self._pending = None
        self._updating = False
        self._loop = asyncio.get_event_loop()

    # Attach a disposable studio-light layer to the stage session.
    def attach(self, stage):
        # Detach any previous stage before creating a new rig.
        if stage == self._stage:
            return
        self.detach()
        if stage is None:
            return

        # --- 1) CREATE AN ISOLATED LIGHTING LAYER ---

        # A dedicated anonymous sublayer can be removed without undoing user edits.
        layer = Sdf.Layer.CreateAnonymous("viewport_light_studio.usda")
        rig_stage = Usd.Stage.Open(layer)
        root_path = "/ViewportLight_Studio"
        # Avoid masking a prim in an opened asset with the same name.
        while stage.GetPrimAtPath(root_path):
            root_path += "_"
        root = UsdGeom.Xform.Define(rig_stage, root_path)
        root.GetPrim().SetCustomDataByKey("omni:kit:hidden", True)
        root.GetPrim().SetCustomDataByKey("omni:kit:no_delete", True)
        root_rotation = root.AddRotateXOp()

        # --- 2) BUILD KEY, FILL, AND RIM LIGHTS ---

        # Broad neutral key, cool fill and rim. No HDR assets or ray-traced shadows.
        lights = (
            ("Key", (-35.0, -35.0, 0.0), (1.0, 0.97, 0.94), 30.0),
            ("Fill", (-20.0, 115.0, 0.0), (0.88, 0.93, 1.0), 16.0),
            ("Rim", (45.0, 180.0, 0.0), (1.0, 1.0, 1.0), 20.0),
        )
        for name, light_rotation, color, intensity in lights:
            light = UsdLux.DistantLight.Define(rig_stage, f"{root_path}/{name}")
            light.CreateIntensityAttr(intensity)
            light.CreateColorAttr(Gf.Vec3f(*color))
            light.CreateAngleAttr(25.0)
            UsdLux.LightAPI(light.GetPrim()).CreateSpecularAttr(0.0)
            UsdLux.ShadowAPI.Apply(light.GetPrim()).CreateShadowEnableAttr(False)
            UsdGeom.Xformable(light).AddRotateXYZOp().Set(Gf.Vec3f(*light_rotation))

        # --- 3) ATTACH THE LAYER AND WATCH STAGE CHANGES ---

        self._stage, self._layer = stage, layer
        self._rig_stage, self._root_path, self._rotation = rig_stage, Sdf.Path(root_path), root_rotation
        session = stage.GetSessionLayer()
        session.subLayerPaths.insert(0, layer.identifier)
        self._refresh()
        self._notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_objects_changed, stage)

    # Queue a light refresh after structure or stage metadata changes.
    def _on_objects_changed(self, notice, _stage):
        if self._updating:
            return
        # Camera and mesh animation only change attribute values. Scan lights on
        # structural edits or stage metadata changes, once on the next update.
        if notice.GetResyncedPaths() or Sdf.Path.absoluteRootPath in notice.GetChangedInfoOnlyPaths():
            self._loop.call_soon_threadsafe(self._schedule_refresh)

    # Queue one refresh for the next Kit update.
    def _schedule_refresh(self):
        if self._stage is not None and (self._pending is None or self._pending.done()):
            self._pending = asyncio.ensure_future(self._refresh_next_update())

    # Refresh the attached rig after the next Kit update.
    async def _refresh_next_update(self):
        await cast(Any, omni.kit.app.get_app()).next_update_async()
        if self._stage is not None:
            self._refresh()

    # Hide authored lights in the temporary layer and align the studio rig.
    def _refresh(self):
        if self._stage is None or self._rig_stage is None or self._rotation is None:
            return
        # Hide scene lights only in our disposable layer. Geometry, materials,
        # the user's edit target and other session edits survive. Newly created
        # lights are handled here too, without a per-frame stage traversal.
        hidden_paths = {
            prim.GetPath()
            for prim in self._stage.Traverse()
            if not prim.GetPath().HasPrefix(self._root_path) and prim.HasAPI(UsdLux.LightAPI)
        }
        self._updating = True
        try:
            # Remove obsolete overrides, then hide the current scene lights.
            for path in self._hidden_paths - hidden_paths:
                self._rig_stage.RemovePrim(path)
            for path in hidden_paths:
                override = self._rig_stage.OverridePrim(path)
                UsdGeom.Imageable(override).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            # Rotate the rig to match Y-up or Z-up stages.
            self._rotation.Set(90 if UsdGeom.GetStageUpAxis(self._stage) == UsdGeom.Tokens.z else 0)
            self._hidden_paths = hidden_paths
        finally:
            self._updating = False

    # Remove the temporary light layer and release its listeners.
    def detach(self):
        # Stop notices and queued refreshes before removing the session sublayer.
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        if self._stage is not None and self._layer is not None:
            session = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
        self._stage = None
        self._layer = None
        self._rig_stage = None
        self._root_path = None
        self._rotation = None
        self._hidden_paths.clear()
