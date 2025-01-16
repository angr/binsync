import logging
import sys
import threading

from libbs.ui.version import set_ui_version
set_ui_version("PySide6")
from libbs.ui.qt_objects import QMainWindow, QApplication
from libbs.api import DecompilerInterface
from libbs.decompilers import GHIDRA_DECOMPILER

from binsync.ui.control_panel import ControlPanel
from binsync.ui.config_dialog import ConfigureBSDialog
from binsync.controller import BSController

l = logging.getLogger(__name__)


class ControlPanelWindow(QMainWindow):
    """
    The class for the window that shows changes/info to BinSync data. This includes things like
    changes to functions or structs.
    """

    def __init__(self):
        super(ControlPanelWindow, self).__init__()
        self.setWindowTitle("BinSync")
        self.width_hint = 300

        print("Initing interface")
        self._interface = DecompilerInterface.discover(force_decompiler=GHIDRA_DECOMPILER, headless=True)
        self.controller = BSController(decompiler_interface=self._interface)
        self.control_panel = ControlPanel(self.controller)
        self._init_widgets()

    def _init_widgets(self):
        self.control_panel.show()
        self.setCentralWidget(self.control_panel)

    #
    # handlers
    #

    def configure(self):
        config = ConfigureBSDialog(self.controller)
        config.show()
        config.exec_()
        return self.controller.check_client()

    def closeEvent(self, event):
        self.controller.shutdown()


def start_ghidra_ui():
    print("in start_ghidra_ui")
    # detect if we are on macos
    if sys.platform == "darwin":
        from PyObjCTools.AppHelper import callAfter
        # Schedule the GUI creation to run on the main thread
        callAfter(_start_ghidra_ui_core)
    else:
        _start_ghidra_ui_core()


def _start_ghidra_ui_core():
    import remote_pdb; remote_pdb.RemotePdb("localhost", 4444).set_trace()
    print("QApplication being made!")
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    # Prevent the application from quitting when the last window is closed
    app.setQuitOnLastWindowClosed(False)
    cp_window = ControlPanelWindow()

    # control panel should stay hidden until a good config happens
    cp_window.hide()
    connected = cp_window.configure()
    if connected:
        cp_window.show()
    else:
        sys.exit(1)

    if not app.property("eventLoopRunning"):
        app.setProperty("eventLoopRunning", True)
    app.exec()


