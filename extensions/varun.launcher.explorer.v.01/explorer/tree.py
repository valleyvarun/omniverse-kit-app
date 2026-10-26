"""Lazy folder-tree state, independent of launcher stages and applications."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import filesystem as fs


@dataclass(eq=False)
class Node:
    url: str
    name: str
    directory: bool
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)
    expanded: bool = False
    loaded: bool = False
    loading: bool = False
    error: str = ""
    read_finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class ExplorerTree:
    def __init__(self, changed: Callable[[], None]) -> None:
        self.root: Node | None = None
        self.selected: Node | None = None
        self.opening: bool = False
        self.error: str = ""
        self._changed: Callable[[], None] = changed
        self._alive: bool = True
        self._generation: int = 0
        self._tasks: set[asyncio.Future[None]] = set()

    def schedule(self, work: Awaitable[None]) -> None:
        task: asyncio.Future[None] = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def open_folder(self, path: str) -> None:
        self._generation += 1
        generation: int = self._generation
        self.opening = True
        self.error = ""
        self._changed()
        error: OSError | ValueError
        try:
            url: str = fs.normalize(path)
            await fs.verify_directory(url)
            entries: list[fs.Entry] = await fs.read_directory(url)
            if not self._alive or generation != self._generation:
                return
            root: Node = Node(url, fs.display_name(url), True, expanded=True, loaded=True)
            self._replace_children(root, entries)
            self.root = self.selected = root
        except (OSError, ValueError) as error:
            if generation == self._generation:
                self.error = str(error)
        finally:
            if self._alive and generation == self._generation:
                self.opening = False
                self._changed()

    def close_folder(self) -> None:
        self._generation += 1
        self.root = self.selected = None
        self.opening = False
        self.error = ""
        self._changed()

    def select(self, node: Node) -> None:
        self.selected = node
        self._changed()

    def toggle(self, node: Node) -> None:
        self.selected = node
        if node.directory:
            node.expanded = not node.expanded
            if node.expanded:
                self.schedule(self.load(node))
        self._changed()

    async def load(self, node: Node) -> None:
        generation: int = self._generation
        while node.loading:
            await node.read_finished.wait()
        if not self._alive or generation != self._generation:
            return
        node.loading = True
        node.read_finished.clear()
        node.error = ""
        self._changed()
        error: OSError | ValueError
        try:
            entries: list[fs.Entry] = await fs.read_directory(node.url)
            if self._alive and generation == self._generation:
                self._replace_children(node, entries)
                node.loaded = True
        except (OSError, ValueError) as error:
            node.error = str(error)
        finally:
            node.loading = False
            node.read_finished.set()
            if self._alive and generation == self._generation:
                self._changed()

    def _replace_children(self, parent: Node, entries: list[fs.Entry]) -> None:
        old: dict[str, Node] = {}
        child: Node
        for child in parent.children:
            old[child.url] = child
        children: list[Node] = []
        entry: fs.Entry
        for entry in entries:
            child = old.get(entry.url) or Node(entry.url, entry.name, entry.directory, parent)
            if child.directory != entry.directory:
                child = Node(entry.url, entry.name, entry.directory, parent)
            children.append(child)
        parent.children = children
        selected: Node | None = self.selected
        while selected is not None and selected.parent is not parent:
            selected = selected.parent
        if selected is not None and selected not in children:
            self.selected = parent

    def destination(self) -> Node | None:
        selected: Node | None = self.selected
        if selected is None:
            return self.root
        return selected if selected.directory else selected.parent

    async def create(self, parent: Node, name: str, directory: bool) -> None:
        generation: int = self._generation
        entry: fs.Entry = await fs.create_entry(parent.url, name, directory)
        if not self._alive or generation != self._generation:
            return
        parent.expanded = True
        await self.load(parent)
        if not self._alive or generation != self._generation:
            return
        child: Node
        for child in parent.children:
            if child.url == entry.url:
                self.selected = child
                break
        self._changed()

    def rows(self) -> list[tuple[Node, int]]:
        result: list[tuple[Node, int]] = []

        def append(node: Node, depth: int) -> None:
            result.append((node, depth))
            if node.expanded:
                child: Node
                for child in node.children:
                    append(child, depth + 1)

        if self.root is not None:
            append(self.root, 0)
        return result

    async def refresh(self) -> None:
        node: Node
        depth: int
        for node, depth in self.rows():
            if node.directory and node.expanded:
                await self.load(node)

    def destroy(self) -> None:
        self._alive = False
        self._generation += 1
        task: asyncio.Future[None]
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
