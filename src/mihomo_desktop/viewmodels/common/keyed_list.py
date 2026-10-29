"""A list model over rows that carry an ``id``, brought up to date in place.

``sync`` turns a fresh list into the fewest model signals it can: removals, one reorder,
insertions and a single dataChanged for the rest. Views keep their delegates and scroll
position, so a table refreshed every second does not rebuild itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

type Index = QModelIndex | QPersistentModelIndex

LAYOUT_SIGNATURE = ("QList<QPersistentModelIndex>", "QAbstractItemModel::LayoutChangeHint")


class KeyedListModel(QAbstractListModel):
    def __init__(self, roles: Sequence[str], parent: QObject | None = None) -> None:
        """``roles`` name the row attributes QML reads, one role each."""
        super().__init__(parent)
        first = Qt.ItemDataRole.UserRole + 1
        self._roles = {first + n: name for n, name in enumerate(roles)}
        self._rows: list[Any] = []

    @property
    def rows(self) -> Sequence[Any]:
        return self._rows

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:
        return {role: QByteArray(name.encode()) for role, name in self._roles.items()}

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        name = self._roles.get(role)
        if name is None or not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return getattr(self._rows[index.row()], name)

    def sync(self, rows: Sequence[Any]) -> None:
        """Make the model show ``rows``; rows match by ``id`` and compare with ``==``."""
        wanted = {row.id for row in rows}
        self._remove_missing(wanted)
        kept = {row.id for row in self._rows}
        self._reorder([row.id for row in rows if row.id in kept])
        self._insert_new(rows, kept)
        pairs = zip(self._rows, rows, strict=True)
        changed = [n for n, (old, new) in enumerate(pairs) if old != new]
        self._rows = list(rows)
        if changed:
            self.dataChanged.emit(self.index(changed[0]), self.index(changed[-1]))

    def _remove_missing(self, wanted: set[str]) -> None:
        end = len(self._rows)
        while end > 0:
            if self._rows[end - 1].id in wanted:
                end -= 1
                continue
            start = end - 1
            while start > 0 and self._rows[start - 1].id not in wanted:
                start -= 1
            self.beginRemoveRows(QModelIndex(), start, end - 1)
            del self._rows[start:end]
            self.endRemoveRows()
            end = start

    def _reorder(self, order: list[str]) -> None:
        if [row.id for row in self._rows] == order:
            return
        # A vertical sort lets QML views rebind their delegates instead of rebuilding them.
        # PySide6 picks the overload without arguments unless the signature is spelt out.
        hint = QAbstractItemModel.LayoutChangeHint.VerticalSortHint
        self.layoutAboutToBeChanged[LAYOUT_SIGNATURE].emit([], hint)  # pyright: ignore[reportIndexIssue]
        position = {row_id: n for n, row_id in enumerate(order)}
        persistent = self.persistentIndexList()
        moved = [self.index(position[self._rows[i.row()].id]) for i in persistent]
        self._rows.sort(key=lambda row: position[row.id])
        self.changePersistentIndexList(persistent, moved)
        self.layoutChanged[LAYOUT_SIGNATURE].emit([], hint)  # pyright: ignore[reportIndexIssue]

    def _insert_new(self, rows: Sequence[Any], kept: set[str]) -> None:
        # Everything before each run of new rows is already in place, so the run goes in at
        # its final position.
        n = 0
        while n < len(rows):
            if rows[n].id in kept:
                n += 1
                continue
            start = n
            while n < len(rows) and rows[n].id not in kept:
                n += 1
            self.beginInsertRows(QModelIndex(), start, n - 1)
            self._rows[start:start] = rows[start:n]
            self.endInsertRows()
