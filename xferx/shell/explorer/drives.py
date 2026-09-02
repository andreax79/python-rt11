from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from xferx.abstract import XFERXPath

from .messages import DriveChanged


class DriveLabel(Static):
    DEFAULT_CSS = """
    DriveLabel {
        width: auto;
        height: 1;
        padding: 0;
        margin: 0 1 0 1;
        background: white;
        color: black;
    }

    DriveLabel:hover {
        background: #0000AA;
        color: white;
    }

    DriveLabel.selected {
        background: #0000AA;
        color: white;
    }
    """

    drive: str

    def __init__(self, drive: str, **kwargs: object) -> None:
        self.drive = drive.upper()
        super().__init__(f"[{self.drive}:]", **kwargs)  # type: ignore

    def on_click(self) -> None:
        selector = self.parent
        if isinstance(selector, DriveSelector):
            selector.select_drive(self.drive)


class DriveSelector(Horizontal):
    DEFAULT_CSS = """
    DriveSelector {
        width: 100%;
        height: 1;
        padding: 0;
        margin: 0 0 1 0;
        background: white;
        color: black;
    }
    """

    drives: list[str]
    selected_drive: str
    current_directories: dict[str, XFERXPath]

    def __init__(
        self,
        drives: list[str] | None = None,
        selected: str = "DK",
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore
        self.drives = [drive.upper() for drive in drives or []]
        self.selected_drive = selected.upper()
        self.current_directories = {}

    def get_current_directory(self, drive: str) -> Optional[XFERXPath]:
        return self.current_directories.get(drive.upper())

    def set_current_directory(self, drive: str, directory: XFERXPath) -> None:
        self.current_directories[drive.upper()] = directory

    def compose(self) -> ComposeResult:
        for drive in self.drives:
            yield DriveLabel(drive, id=f"drive-{drive.lower()}")

    def on_mount(self) -> None:
        self.select_drive(self.selected_drive, notify=False)

    def select_drive(self, drive: str, notify: bool = True) -> None:
        drive = drive.upper()
        if drive not in self.drives:
            return

        self.selected_drive = drive
        for label in self.query(DriveLabel):
            label.remove_class("selected")

        self.query_one(f"#drive-{drive.lower()}", DriveLabel).add_class("selected")
        if notify:
            self.post_message(DriveChanged(drive))
