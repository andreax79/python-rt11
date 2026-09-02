from __future__ import annotations

from textual.message import Message


class DriveChanged(Message):
    def __init__(self, drive: str) -> None:
        super().__init__()
        self.drive = drive
