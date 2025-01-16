import logging
import typing

# pylint: disable=wrong-import-position,wrong-import-order
from libbs.ui.version import set_ui_version
set_ui_version("PySide6")

from libbs.ui.qt_objects import QVBoxLayout
from libbs.decompilers.angr.compat import GenericBSAngrManagementPlugin

from angrmanagement.ui.views.view import BaseView
from PySide6QtAds import SideBarRight, CDockWidget, CDockManager

from binsync.ui.control_panel import ControlPanel
from binsync.controller import BSController
from binsync.ui.config_dialog import ConfigureBSDialog

if typing.TYPE_CHECKING:
    from angrmanagement.ui.workspace import Workspace


_l = logging.getLogger(__name__)


class ControlPanelView(BaseView):
    """
    The class for the window that shows changes/info to BinSync data. This includes things like
    changes to functions or structs.
    """

    def __init__(self, workspace, default_docking_position, controller, *args, **kwargs):
        super().__init__('sync', workspace, default_docking_position)
        self.base_caption = "BinSync: Control Panel"
        self.controller: BSController = controller
        self.control_panel = ControlPanel(self.controller)
        self._init_widgets()
        self.width_hint = 300

    def reload(self):
        pass

    def close(self):
        self.hide()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def _init_widgets(self):
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.control_panel)
        self.setLayout(main_layout)


class BinsyncPlugin(GenericBSAngrManagementPlugin):
    """
    Controller plugin for BinSync
    """
    def __init__(self, workspace: "Workspace"):
        """
        The entry point for the BinSync plugin. This class is responsible for both initializing the GUI and
        deiniting it as well. The BinSync plugin also starts the BinsyncController, which is a threaded class
        that pushes and pulls changes every so many seconds.

        @param workspace:   an AM _workspace (usually found in _instance)
        """
        super().__init__(workspace)
        # construct the controller and control panel
        self.controller = BSController(decompiler_interface=self.interface)
        self.control_panel_view = ControlPanelView(workspace, 'right', self.controller)
        self.controller.control_panel = self.control_panel_view

        self.sync_menu = None
        self.selected_funcs = []

    def teardown(self):
        self.controller.stop_worker_routines()
        del self.controller.deci
        self.workspace.remove_view(self.control_panel_view)

    #
    # BinSync Menu
    #

    MENU_BUTTONS = ('Start Binsync...', 'Toggle Binsync Panel', 'View Progress...')
    MENU_CONFIG_ID = 0
    MENU_TOGGLE_PANEL_ID = 1
    MENU_VIEW_PROGRESS_ID = 2

    def handle_click_menu(self, idx):
        # sanity check on menu selection
        if idx < 0 or idx >= len(self.MENU_BUTTONS):
            return

        if self.workspace.main_instance.project.am_none:
            return

        mapping = {
            self.MENU_CONFIG_ID: self.open_sync_config_dialog,
            self.MENU_TOGGLE_PANEL_ID: self.toggle_sync_panel,
            self.MENU_VIEW_PROGRESS_ID: self.open_view_progress_window,
        }

        # call option mapped to each menu pos
        mapping.get(idx)()

    def open_sync_config_dialog(self):
        if self.workspace.main_instance.project.am_none:
            # project does not exist yet
            return

        sync_config = ConfigureBSDialog(self.controller)
        sync_config.exec_()
        if self.controller.check_client() and self.control_panel_view not in self.workspace.view_manager.views:
            self.workspace.add_view(self.control_panel_view)
            dock = self.workspace.view_manager.view_to_dock[self.control_panel_view]
            dock.setAutoHide(True, SideBarRight)
            dock.closed.disconnect()
            dock.setFeature(CDockWidget.DockWidgetDeleteOnClose, False)
            # grab the dock manager by climbing up parents, probably a better way to directly grab it
            dm = dock.parent().parent().parent()
            assert(isinstance(dm, CDockManager))
            dm.setAutoHideConfigFlag(CDockManager.AutoHideHasCloseButton, False)

    def toggle_sync_panel(self):
        self.workspace.view_manager.view_to_dock[self.control_panel_view].toggleView()

    def open_view_progress_window(self):
        from binsync.ui.progress_window import open_progress_window
        open_progress_window(self.controller)
