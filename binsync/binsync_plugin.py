# A cross-decompiler collaboration plugin
# @author BinSync Team
# @category Collaboration
# @menupath Tools.BinSync.Start UI...
#@runtime PyGhidra

import sys

def create_plugin(*args, **kwargs):
    print("in creator")
    # REPLACE_ME this import with an import of your plugin's create_plugin function
    from binsync import create_plugin as _create_plugin
    print(sys.executable)
    print("running private create")
    return _create_plugin(*args, **kwargs)

# =============================================================================
# LibBS generic plugin loader (don't touch things below this)
# =============================================================================

import sys
# Python 2 has special requirements for Ghidra, which forces us to use a different entry point
# and scope for defining plugin entry points.
# The Python 3 side has been edited since currently every supported decompiler must import create_plugin
if sys.version[0] == "2":
    # Do Ghidra Py2 entry point
    import subprocess
    from libbs_vendored.ghidra_bridge_server import GhidraBridgeServer

    GhidraBridgeServer.run_server(background=True)
    process = subprocess.Popen(plugin_command.split(" "))
    if process.poll() is not None:
        raise RuntimeError("Failed to run the Python3 backed. It's likely Python3 is not in your Path inside Ghidra.")
else:
    create_plugin()


def PLUGIN_ENTRY(*args, **kwargs):
    """
    This is the entry point for IDA to load the plugin.
    """
    return create_plugin(*args, **kwargs)
