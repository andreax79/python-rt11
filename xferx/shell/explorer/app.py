from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from textual.widgets import Tree

from xferx.abstract import XFERXPath
from xferx.shell.kmon import Shell

from .drives import DriveSelector
from .files import FileTable
from .messages import DriveChanged
from .tree import FileTree


class Explorer(App[None]):
    CSS = """
    Screen {
        background: white;
        color: black;
        layout: vertical;
    }

    #drive-path {
        height: 1;
        background: white;
        color: black;
        padding: 0 1;
    }

    #panes {
        height: 1fr;
        width: 100%;
        background: white;
        padding: 0 1;
    }

    .pane {
        height: 100%;
        border: none;
        background: white;
        color: black;
    }

    #tree-pane {
        width: 45%;
        margin-right: 1;
    }

    #files-pane {
        width: 55%;
    }

    .pane-title {
        height: 1;
        background: #AAAAAA;
        color: black;
        text-align: center;
        padding: 0;
    }

    .files-title {
        background: #0000AA;
        color: white;
        text-align: center;
    }

    Tree {
        height: 1fr;
        background: white;
        color: black;
        border: none;
        padding: 0;
    }

    Tree:focus {
        border: none;
    }

    DataTable {
        height: 1fr;
        background: white;
        color: black;
        border: none;
    }

    DataTable:focus {
        border: none;
    }

    DataTable > .datatable--header {
        display: none;
    }

    DataTable > .datatable--cursor {
        background: #0000AA;
        color: white;
    }

    #status {
        height: 1;
        background: #AAAAAA;
        color: black;
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "quit", "Quit"),
    ]

    shell: Shell

    def __init__(self, shell: Shell, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore
        self.shell = shell

    def compose(self) -> ComposeResult:
        yield Static(self.shell.volumes.get_pwd(), id="drive-path")

        yield DriveSelector(
            drives=self.shell.volumes.get_volumes(include_logical=True),
            selected=self.shell.volumes.get_default_volume(),
        )

        with Horizontal(id="panes"):
            with Vertical(classes="pane", id="tree-pane"):
                yield Static("Directory Tree", classes="pane-title")
                yield FileTree(self.shell, id="directory-tree")

            with Vertical(classes="pane", id="files-pane"):
                yield Static(self._files_title(), classes="pane-title files-title", id="files-title")
                yield FileTable(self.shell, id="files", cursor_type="row")

        yield Static(
            "",
            id="status",  # TODO
        )

    def on_mount(self) -> None:
        self.refresh_volume_view()

    def on_drive_changed(self, message: DriveChanged) -> None:
        try:
            self.shell.volumes.set_default_volume(message.drive, cmd="EXPLORER")
        except Exception as exc:
            self.query_one("#status", Static).update(str(exc))
            return
        self.shell.update_prompt()
        self.refresh_volume_view()

    def on_tree_node_selected(self, event: Tree.NodeSelected[object]) -> None:
        entry = event.node.data
        if not isinstance(entry, XFERXPath):
            return
        selector = self.query_one(DriveSelector)
        selector.set_current_directory(selector.selected_drive, entry)
        path = f"{selector.selected_drive}:{entry.fullname}"
        self.query_one("#drive-path", Static).update(path)
        self.query_one("#files-title", Static).update(path)
        self.query_one(FileTable).reload_directory(entry)

    def refresh_volume_view(self) -> None:
        selector = self.query_one(DriveSelector)
        drive = selector.selected_drive
        directory = selector.get_current_directory(drive)
        if directory is None:
            fs = self.shell.volumes.get_volume(drive, cmd="EXPLORER")
            pwd = fs.get_pwd()
            try:
                directory = fs.get_file_entry(pwd) if pwd else fs.get_root()
            except Exception:
                directory = fs.get_root()
            selector.set_current_directory(drive, directory)

        path = f"{drive}:{directory.fullname}"
        self.query_one("#drive-path", Static).update(path)
        self.query_one("#files-title", Static).update(path)
        self.query_one(FileTree).reload_volume(directory)
        self.query_one(FileTable).reload_directory(directory)

    def _files_title(self) -> str:
        return f"{self.shell.volumes.get_default_volume()}:*.*"
