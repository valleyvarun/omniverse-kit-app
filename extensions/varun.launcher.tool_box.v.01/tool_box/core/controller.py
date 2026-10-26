"""Own the tools and coordinate their shared activation state."""

from tool_box.core.resources import ICONS_DIR
from tool_box.core.tool import Tool
from tool_box.shortcuts import SHORTCUTS
from tool_box.tools.drawing.curves.tool import ThreeDDrawCurvesTool
from tool_box.tools.drawing.plane.grid import PlaneGridManager
from tool_box.tools.drawing.plane.tool import DrawingPlane
from tool_box.tools.drawing.points.tool import ThreeDDrawTool
from tool_box.tools.transform.tools import OP_SELECT, TransformTools, set_transform_op


class ToolController:
    def __init__(self) -> None:
        self.transforms: TransformTools = TransformTools()
        self.points: ThreeDDrawTool = ThreeDDrawTool()
        self.curves: ThreeDDrawCurvesTool = ThreeDDrawCurvesTool()
        self.plane: DrawingPlane = DrawingPlane()
        transform_tools: list[Tool] = self.transforms.make_tools()
        self.tools: dict[str, Tool] = dict(zip(("move", "rotate", "scale"), transform_tools))
        self.tools.update(
            draw_points=self.points.make_tool(),
            draw_curves=self.curves.make_tool(),
            drawing_plane=self.plane.make_tool(),
        )
        # Packaged PNGs replace the drawing tools' placeholder labels.
        action_id: str
        icon_name: str
        for action_id, icon_name in (
            ("draw_points", "brush_oval.png"),
            ("draw_curves", "brush_sphere.png"),
            ("drawing_plane", "drawing_plane.png"),
        ):
            self.tools[action_id].icon = str(ICONS_DIR / icon_name)
            self.tools[action_id].icon_text = None
        tool: Tool
        for action_id, tool in self.tools.items():
            tool.shortcut = SHORTCUTS.get(action_id)
        self.grid: PlaneGridManager = PlaneGridManager()
        self.grid.apply()

    # Escape and viewport switches finish drawing before changing tools.
    def cancel(self) -> None:
        self.points.deactivate()
        self.curves.deactivate()
        set_transform_op(OP_SELECT)

    def destroy(self) -> None:
        self.points.destroy()
        self.curves.destroy()
        self.transforms.destroy()
        self.grid.destroy()
        self.tools.clear()
