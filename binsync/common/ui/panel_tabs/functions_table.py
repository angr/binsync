import datetime
import logging
import time
from typing import Dict

from binsync.common.controller import BinSyncController
from binsync.common.ui.panel_tabs.table_model import BinsyncTableModel, BinsyncTableFilterLineEdit, BinsyncTableView
from binsync.common.ui.qt_objects import (
    QMenu,
    QAction,
    QWidget,
    QVBoxLayout,
    QColor,
    Qt
)
from binsync.common.ui.utils import friendly_datetime
from binsync.core.scheduler import SchedSpeed
from binsync.data import Function

l = logging.getLogger(__name__)


class FunctionTableModel(BinsyncTableModel):
    def __init__(self, controller: BinSyncController, col_headers=None, filter_cols=None, time_col=None,
                 addr_col=None, parent=None):
        super().__init__(controller, col_headers, filter_cols, time_col, addr_col, parent)
        self.data_dict = {}
        self.saved_color_window = self.controller.table_coloring_window
        self.context_menu_cache = {}

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return hex(self.row_data[index.row()][col])
            elif col == 1 or col == 2:
                return self.row_data[index.row()][col]
            elif col == 3:
                return friendly_datetime(self.row_data[index.row()][col])
        elif role == self.SortRole:
            return self.row_data[index.row()][col]
        elif role == Qt.BackgroundRole:
            return self.data_bgcolors[index.row()]
        elif role == self.FilterRole:
            return f"{hex(self.row_data[0][col])} {self.row_data[1][col]} {self.row_data[2][col]}"
        elif role == Qt.ToolTipRole:
            return self.data_tooltips[index.row()]
        return None

    def update_table(self):
        cmenu_cache = {}

        touched_addrs = []
        # grab all the new info from user states
        for user in self.controller.users():
            state = self.controller.client.get_state(user=user.name)
            user_funcs: Dict[int, Function] = state.functions
            for func_addr, sync_func in user_funcs.items():
                func_change_time = sync_func.last_change
                # don't add functions that were never changed by the user
                if not func_change_time:
                    continue

                if func_addr in cmenu_cache:
                    cmenu_cache[func_addr].append(user.name)
                else:
                    cmenu_cache[func_addr] = [user.name]

                exists = func_addr in self.data_dict
                if exists:
                    if not func_change_time or (func_change_time <= self.data_dict[func_addr][self.time_col]):
                        continue
                row = [func_addr, sync_func.name if sync_func.name else "", user.name,
                       func_change_time]

                self.data_dict[func_addr] = row
                touched_addrs.append(func_addr)

        self.context_menu_cache = cmenu_cache
        # parse new info to figure out what specifically needs updating, recalculate tooltips/coloring
        data_to_send = []
        colors_to_send = []
        idxs_to_update = []
        for i, (k, v) in enumerate(self.data_dict.items()):
            if k in touched_addrs:
                idxs_to_update.append(i)
            data_to_send.append(v)

            duration = time.time() - v[self.time_col]  # table coloring
            row_color = None
            if 0 <= duration <= self.controller.table_coloring_window:
                opacity = (self.controller.table_coloring_window - duration) / self.controller.table_coloring_window
                row_color = QColor(BinsyncTableModel.ACTIVE_FUNCTION_COLOR[0],
                                   BinsyncTableModel.ACTIVE_FUNCTION_COLOR[1],
                                   BinsyncTableModel.ACTIVE_FUNCTION_COLOR[2],
                                   int(BinsyncTableModel.ACTIVE_FUNCTION_COLOR[3] * opacity))
            colors_to_send.append(row_color)

            self.data_tooltips.append(f"Age: {friendly_datetime(v[self.time_col])}")

        if len(data_to_send) != self.rowCount():
            idxs_to_update = []

        if self.controller.table_coloring_window != self.saved_color_window:
            self.saved_color_window = self.controller.table_coloring_window
            idxs_to_update = range(len(data_to_send))

        self.update_signal.emit(data_to_send, colors_to_send)

        for idx in idxs_to_update:
            self.dataChanged.emit(self.index(0, idx), self.index(self.rowCount() - 1, idx))


class FunctionTableView(BinsyncTableView):
    HEADER = ['Addr', 'Remote Name', 'User', 'Last Push']

    def __init__(self, controller: BinSyncController, filteredit: BinsyncTableFilterLineEdit, stretch_col=None,
                 col_count=None, parent=None):
        super().__init__(controller, filteredit, stretch_col, col_count, parent)

        self.model = FunctionTableModel(controller, self.HEADER, filter_cols=[0, 1], time_col=3, addr_col=0,
                                        parent=parent)
        self.proxymodel.setSourceModel(self.model)
        self.setModel(self.proxymodel)

        # always init settings *after* loading the model
        self._init_settings()

    def _get_valid_users_for_func(self, func_addr):
        """ Helper function for getting users that have changes in a given function """
        if func_addr in self.model.context_menu_cache:
            for username in self.model.context_menu_cache[func_addr]:
                yield username
        else:
            l.info("fetching")
            for user in self.controller.client.check_cache_(self.controller.client.users,
                                                            priority=SchedSpeed.FAST, no_cache=False):
                # only populate with cached items to prevent main thread waiting on atomic actions
                cache_item = self.controller.client.check_cache_(self.controller.client.get_state, user=user.name,
                                                                 priority=SchedSpeed.FAST)
                if cache_item is not None:
                    user_state = cache_item
                else:
                    continue

                user_func = user_state.get_function(func_addr)

                # function must be changed by this user
                if not user_func or not user_func.last_change:
                    continue

                yield user.name

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setObjectName("binsync_function_table_context_menu")
        valid_row = True
        selected_row = self.rowAt(event.pos().y())
        idx = self.proxymodel.index(selected_row, 0)
        idx = self.proxymodel.mapToSource(idx)
        # support for automated tests
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

        if valid_row:
            func_addr = self.model.row_data[idx.row()][0]
            user_name = self.model.row_data[idx.row()][2]

            menu.addSeparator()
            if isinstance(func_addr, int) and func_addr > 0:
                menu.addAction("Sync", lambda: self.controller.fill_function(func_addr, user=user_name))
            from_menu = menu.addMenu(f"Sync from...")
            users = self._get_valid_users_for_func(func_addr)
            for username in users:
                action = from_menu.addAction(username)
                action.triggered.connect(
                    lambda chck, name=username: self.controller.fill_function(func_addr, user=name))
        menu.popup(self.mapToGlobal(event.pos()))


class QFunctionTable(QWidget):
    """ Wrapper widget to contain the function table classes in one file (prevents bulking up control_panel.py) """

    def __init__(self, controller: BinSyncController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._init_widgets()

    def _init_widgets(self):
        self.filteredit = BinsyncTableFilterLineEdit(parent=self)
        self.table = FunctionTableView(self.controller, self.filteredit, stretch_col=1, col_count=4)
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addWidget(self.filteredit)
        self.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

    def update_table(self):
        self.table.update_table()

    def reload(self):
        pass
