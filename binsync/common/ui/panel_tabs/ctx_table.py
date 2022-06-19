import logging

from binsync.common.controller import BinSyncController
from binsync.common.ui.qt_objects import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QAction,
    QFontDatabase
)
from binsync.common.ui.utils import QNumericItem, friendly_datetime

l = logging.getLogger(__name__)

fixed_width_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
fixed_width_font.setPixelSize(14)

class QCTXItem:
    """
    The CTX view shown in the Control Panel. Responsible for showing the main user info on whatever the main user
    is currently looking at (clicked). For any line in a function, this would be the entire function. For a struct,
    this would be a struct. The view will be as useful as the decompilers support for understanding what the user
    is looking at.

    TODO: refactor this to allow for any context item, not just functions (like structs).
    """
    def __init__(self, user, name, last_push, changes):
        self.user = user
        self.name = name
        self.last_push = last_push
        self.changes = changes

    def widgets(self):
        user = QTableWidgetItem(self.user)
        name = QTableWidgetItem(self.name)

        # sort by unix value
        last_push = QNumericItem(friendly_datetime(self.last_push))
        last_push.setData(Qt.UserRole, self.last_push)

        changes = QNumericItem(self.changes)
        changes.setData(Qt.UserRole, self.changes)

        widgets = [
            user,
            name,
            last_push,
            changes
        ]

        for w in widgets:
            w.setFont(fixed_width_font)
            w.setFlags(w.flags() & ~Qt.ItemIsEditable)

        return widgets


class QCTXTable(QTableWidget):

    HEADER = [
        'User',
        'Remote Name',
        'Last Push',
        'Changes'
    ]

    def __init__(self, controller: BinSyncController, parent=None):
        super(QCTXTable, self).__init__(parent)
        self.controller = controller
        self.items = []
        self.ctx = None

        # header
        self.setColumnCount(len(self.HEADER))
        self.column_visibility = [True for _ in range(len(self.HEADER))]
        self.setHorizontalHeaderLabels(self.HEADER)

        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.horizontalHeader().setHorizontalScrollMode(self.ScrollPerPixel)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignHCenter | Qt.Alignment(Qt.TextWordWrap))
        self.horizontalHeader().setMinimumWidth(160)
        self.horizontalHeader().setSortIndicator(2, Qt.DescendingOrder)
        self.setHorizontalScrollMode(self.ScrollPerPixel)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)

        self.setSortingEnabled(True)

    def reload(self):
        self.setSortingEnabled(False)
        self.setRowCount(len(self.items))

        for idx, item in enumerate(self.items):
            for i, it in enumerate(item.widgets()):
                self.setItem(idx, i, it)

        self.viewport().update()
        self.setSortingEnabled(True)

    def _col_hide_handler(self, index):
        self.column_visibility[index] = not self.column_visibility[index]
        self.setColumnHidden(index, self.column_visibility[index])
        if self.column_visibility[index]:
            self.showColumn(index)
        else:
            self.hideColumn(index)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setObjectName("binsync_context_table_context_menu")
        valid_row = True


        func_addr = self.ctx if self.ctx else None
        selected_row = self.rowAt(event.pos().y())
        item = self.item(selected_row, 0)
        if item is None:
            valid_row = False

        col_hide_menu = menu.addMenu("Show Columns")
        handler = lambda ind: lambda: self._col_hide_handler(ind)
        for i, c in enumerate(self.HEADER):
            act = QAction(c, parent=menu)
            act.setCheckable(True)
            act.setChecked(self.column_visibility[i])
            act.triggered.connect(handler(i))
            col_hide_menu.addAction(act)

        if valid_row:
            username = item.text()
            menu.addSeparator()
            menu.addAction("Sync", lambda: self.controller.fill_function(func_addr, user=username))

        menu.popup(self.mapToGlobal(event.pos()))


    def update_table(self, new_ctx=None):
        # only functions currently supported
        if self.ctx is None and new_ctx is None:
            return

        self.ctx = new_ctx or self.ctx
        self.items = []
        for user in self.controller.users():
            state = self.controller.client.get_state(user=user.name)

            func = state.get_function(self.ctx)

            if not func or not func.last_change:
                continue

            # changes is not currently supported
            self.items.append(
                QCTXItem(user.name, func.name, func.last_change, 0)
            )
