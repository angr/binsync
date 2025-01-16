__version__ = "5.2.0"


#
# logging
#

import logging

from binsync.interface_overrides.ghidra import start_ghidra_ui

logging.getLogger("binsync").addHandler(logging.NullHandler())
from binsync.loggercfg import Loggers
loggers = Loggers()
del Loggers
del logging


def create_plugin(*args, **kwargs):
    from libbs.api import DecompilerInterface
    from libbs.decompilers import IDA_DECOMPILER, ANGR_DECOMPILER, BINJA_DECOMPILER, GHIDRA_DECOMPILER

    # First discover the current decompiler and grab the overrides for BinSync specific UI
    current_decompiler = DecompilerInterface.find_current_decompiler()
    if current_decompiler == IDA_DECOMPILER:
        from binsync.interface_overrides.ida import IDABSInterface
        deci_cls = IDABSInterface
    elif current_decompiler == BINJA_DECOMPILER:
        from binsync.interface_overrides.binja import BinjaBSInterface
        deci_cls = BinjaBSInterface
    elif current_decompiler == ANGR_DECOMPILER:
        # angr: special cased since BinSync is shipped in angr
        pass
        deci_cls = None
    elif current_decompiler == GHIDRA_DECOMPILER:
        pass
        print("Starting Ghidra UI")
        start_ghidra_ui()
        deci_cls = None
    else:
        raise ValueError(f"Unknown decompiler {current_decompiler}")

    # We will now create the plugin in the decompiler, which will create the Control Panel in the UI of the specified
    # decompiler. That Control Panel will be provided a reference to the current constructing deci bellow, which
    # will also be passed to future control panels as they are created.
    if deci_cls is not None:
        deci = deci_cls(
            plugin_name="BinSync",
            init_plugin=True,
            gui_init_args=args,
            gui_init_kwargs=kwargs
        )
        return deci.gui_plugin
