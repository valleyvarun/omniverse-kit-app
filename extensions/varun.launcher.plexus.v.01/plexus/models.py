"""Empty UI models; Plexus node definitions will be added later."""

from omni import ui
from omni.kit.widget.graph import GraphModel


class EmptyGraphModel(GraphModel):
    def _get_nodes(self, item: object | None = None) -> list[object]:
        return []

    # Kit's item proxy passes the node to the property's getter.
    nodes: property = property(_get_nodes)


class EmptyCatalogModel(ui.AbstractItemModel):
    def get_item_children(self, item: ui.AbstractItem | None = None) -> list[ui.AbstractItem]:
        return []

    def get_item_value_model_count(self, item: ui.AbstractItem | None = None) -> int:
        return 1

    def filter_by_text(self, text: str) -> None:
        # There are no catalog entries to filter yet.
        pass
