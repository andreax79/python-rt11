from __future__ import annotations

import typing as t

from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from xferx.abstract import XFERXPath, AbstractFilesystem
from xferx.shell.kmon import Shell


class FileTree(Tree[t.Any]):
    shell: Shell

    def __init__(self, shell: Shell, **kwargs: object) -> None:
        super().__init__("", **kwargs)  # type: ignore
        self.shell = shell

    def on_mount(self) -> None:
        self.reload_volume()

    def reload_volume(self, current_directory: t.Optional[XFERXPath] = None) -> None:
        fs = self._current_filesystem()
        volume = self.shell.volumes.get_default_volume()
        path = current_directory.fullname if current_directory is not None else fs.get_pwd()
        root_dir = fs.get_root()
        self.root.set_label(f"{volume}:{root_dir.name}")
        self.root.data = root_dir
        self.root.remove_children()
        self._load_entries(self.root, root_dir)
        self.root.expand()
        current_path = current_directory.fullname if current_directory is not None else fs.get_pwd()
        for node in self.root.children:
            entry = node.data
            if isinstance(entry, XFERXPath) and entry.fullname == current_path:
                self.select_node(node)
                return

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[t.Any]) -> None:
        node = event.node
        entry = node.data
        if not isinstance(entry, XFERXPath) or getattr(node, "_filesystem_loaded", False):
            return
        node._filesystem_loaded = True  # type: ignore
        node.remove_children()
        try:
            self._load_entries(node, entry)
        except Exception as ex:
            print(ex)

    def _current_filesystem(self) -> AbstractFilesystem:
        return self.shell.volumes.get_volume(self.shell.volumes.get_default_volume(), cmd="EXPLORER")

    def _load_entries(
        self,
        node: TreeNode[t.Any],
        path: XFERXPath,
    ) -> None:
        entries = path.iterdir()
        for entry in sorted(entries, key=lambda item: item.name.upper()):
            if entry.is_dir() and not entry.is_mount() and entry.name not in (".", ".."):
                node.add(entry.name, data=entry, allow_expand=True)
