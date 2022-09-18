import logging
import time
from functools import partial
from typing import Dict
import datetime

from binsync.common.controller import BinSyncController
from binsync.common.ui.panel_tabs.table_model import BinsyncTableModel, BinsyncTableFilterLineEdit, BinsyncTableView
from binsync.common.ui.qt_objects import (
    QMenu,
    QAction,
    QWidget,
    QVBoxLayout,
    QModelIndex,
    QColor,
    Qt
)
from binsync.common.ui.utils import friendly_datetime
from binsync.core.scheduler import SchedSpeed
from binsync.data import Function

l = logging.getLogger(__name__)


class CTXTableModel(BinsyncTableModel):
    def __init__(self, controller: BinSyncController, col_headers=None, filter_cols=None, time_col=None,
                 addr_col=None, parent=None):
        super().__init__(controller, col_headers, filter_cols, time_col, addr_col, parent)
        self.data_dict = {}
        self.saved_color_window = self.controller.table_coloring_window

        self.ctx = None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0 or col == 1:
                return self.row_data[index.row()][col]
            elif col == 2:
                return friendly_datetime(self.row_data[index.row()][col])
        elif role == self.SortRole:
            return self.row_data[index.row()][col]
        elif role == Qt.BackgroundRole:
            return self.data_bgcolors[index.row()]
        elif role == self.FilterRole:
            return self.row_data[0][col] + " " + self.row_data[1][col]
        elif role == Qt.ToolTipRole:
            return self.data_tooltips[index.row()]
        return None

    def update_table(self, new_ctx=None):
        """ Updates the table using the controller's information """
        if self.ctx is None and new_ctx is None:
            return

        if new_ctx and self.ctx != new_ctx:
            self.ctx = new_ctx
            self.data_dict = {}

        touched_users = []
        for user in self.controller.users():
            state = self.controller.client.get_state(user=user.name)
            func = state.get_function(self.ctx)
            if not func or not func.last_change:
                continue

            row = [user.name, func.name, func.last_change]
            self.data_dict[user.name] = row
            touched_users.append(user.name)

        # parse new info to figure out what specifically needs updating, recalculate tooltips/coloring
        data_to_send = []
        colors_to_send = []
        idxs_to_update = []
        for i, (k, v) in enumerate(self.data_dict.items()):
            if k in touched_users:
                idxs_to_update.append(i)
            data_to_send.append(v)

            duration = time.time() - v[self.time_col]  # table coloring
            row_color = None
            if 0 <= duration <= self.controller.table_coloring_window:
                opacity = (
                                      self.controller.table_coloring_window - duration) / self.controller.table_coloring_window
                row_color = QColor(BinsyncTableModel.ACTIVE_FUNCTION_COLOR[0],
                                   BinsyncTableModel.ACTIVE_FUNCTION_COLOR[1],
                                   BinsyncTableModel.ACTIVE_FUNCTION_COLOR[2],
                                   int(BinsyncTableModel.ACTIVE_FUNCTION_COLOR[3] * opacity))
            colors_to_send.append(row_color)

            self.data_tooltips.append(f"Age: {friendly_datetime(v[self.time_col])}")

        # no changes required, dont bother updating
        if len(idxs_to_update) == 0 and self.controller.table_coloring_window == self.saved_color_window:
            return

        if len(data_to_send) != self.rowCount():
            idxs_to_update = []

        if self.controller.table_coloring_window != self.saved_color_window:
            self.saved_color_window = self.controller.table_coloring_window
            idxs_to_update = range(len(data_to_send))

        self.update_signal.emit(data_to_send, colors_to_send)

        for idx in idxs_to_update:
            self.dataChanged.emit(self.index(0, idx), self.index(self.rowCount() - 1, idx))


class QCTXTable(BinsyncTableView):
    HEADER = ['User', 'Remote Name', 'Last Push']

    def __init__(self, controller: BinSyncController, stretch_col=None,
                 col_count=None, parent=None):
        super().__init__(controller, None, 1, 3, parent)

        self.model = CTXTableModel(controller, self.HEADER, filter_cols=[0, 1], time_col=2,
                                        parent=parent)
        self.proxymodel.setSourceModel(self.model)
        self.setModel(self.proxymodel)

        # always init settings *after* loading the model
        self._init_settings()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setObjectName("binsync_context_table_context_menu")

        valid_row = True
        selected_row = self.rowAt(event.pos().y())
        idx = self.proxymodel.index(selected_row, 0)
        idx = self.proxymodel.mapToSource(idx)
        if event.pos().y() == -1 and event.pos().x() == -1:
            idx = self.proxymodel.index(0, 0)
            idx = self.proxymodel.mapToSource(idx)
        elif not (0 <= selected_row < len(self.model.row_data)) or not idx.isValid():
            valid_row = False

        col_hide_menu = menu.addMenu("Show Columns")
        handler = lambda ind: lambda: self._col_hide_handler(ind)
        for i, c in enumerate(self.HEADER):
            act = QAction(c, parent=menu)
            act.setCheckable(True)
            act.setChecked(self.column_visibility[i])
            act.triggered.connect(handler(i))
            col_hide_menu.addAction(act)

        if valid_row and self.model.ctx:
            user_name = self.model.row_data[idx.row()][0]

            menu.addSeparator()
            menu.addAction("Sync", lambda: self.controller.fill_function(self.model.ctx, user=user_name))

        menu.popup(self.mapToGlobal(event.pos()))

    def update_table(self, new_ctx=None):
        """ Update the model of the table with new data from the controller """
        self.model.update_table(new_ctx)