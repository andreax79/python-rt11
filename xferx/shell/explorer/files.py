import typing as t

from textual.widgets import DataTable

from xferx.abstract import XFERXPath
from xferx.shell.kmon import Shell


class FileTable(DataTable[t.Any]):
    shell: Shell

    def __init__(self, shell: Shell, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore
        self.shell = shell

        self._entries: dict[str, XFERXPath] = {}

    def on_mount(self) -> None:
        self.add_columns("Name", "Size", "Date")
        self.cursor_type = "row"

    def reload_directory(self, directory: XFERXPath) -> None:
        try:
            if directory.is_dir():
                self.reload_entries(directory.iterdir())
        except Exception as ex:
            print(ex)

    def reload_entries(
        self,
        entries: t.Iterable[XFERXPath],
    ) -> None:
        self.clear()
        self._entries.clear()

        entries = sorted(entries, key=lambda entry: entry.name)
        for i, entry in enumerate(entries):
            # if entry.is_dir():
            #     continue
            key = str(i)
            self._entries[key] = entry
            self.add_row(
                entry.name,
                self._entry_size(entry),
                self._entry_date(entry),
                key=key,
            )

    def _entry_size(self, entry: XFERXPath) -> str:
        try:
            return f"{entry.get_size():,}"
        except Exception:
            return ""

    def _entry_date(self, entry: XFERXPath) -> str:
        if entry.creation_date is None:
            return ""

        return entry.creation_date.strftime("%m-%d-%y")
