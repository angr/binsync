import shutil
import textwrap
from pathlib import Path

from libbs.plugin_installer import LibBSPluginInstaller, PluginInstaller


class BinSyncInstaller(LibBSPluginInstaller):
    def __init__(self):
        super().__init__(targets=PluginInstaller.DECOMPILERS)
        pkg_files = self.find_pkg_files("binsync")
        if pkg_files is None:
            raise RuntimeError("Could not find binsync package files. Please reinstall or report on GitHub.")

        self.binsync_files = pkg_files
        self.stub_files = pkg_files / "stub_files"

    def display_prologue(self):
        print(textwrap.dedent("""
        Now installing...
         _____ _     _____             
        | __  |_|___|   __|_ _ ___ ___ 
        | __ -| |   |__   | | |   |  _|
        |_____|_|_|_|_____|_  |_|_|___|
                          |___|        
        
        The decompiler-agnostic git-based collaboration tool for reverse engineers.
        """))

    def _copy_plugin_to_path(self, path):
        src = self.binsync_files / "binsync_plugin.py"
        dst = Path(path) / src.name
        self.link_or_copy(src, dst, symlink=True)

    def install_angr(self, path=None, interactive=True):
        self.info("Skipping angr install since BinSync is shipped with angr...")

    def install_ida(self, path=None, interactive=True):
        path = super().install_ida(path=path, interactive=interactive)
        if not path:
            return

        self._copy_plugin_to_path(path)
        return path

    def install_ghidra(self, path=None, interactive=True):
        path = super().install_ghidra(path=path, interactive=interactive)
        if not path:
            return

        self._copy_plugin_to_path(path)
        return path

    def install_binja(self, path=None, interactive=True):
        path = super().install_binja(path=path, interactive=interactive)
        if not path:
            return

        # binja requires a folder for the plugin
        bs_binja_dir = path / "binsync"
        if bs_binja_dir.exists():
            shutil.rmtree(bs_binja_dir)
        bs_binja_dir.mkdir()

        # copy things to the new folder
        self.link_or_copy(self.stub_files / "__init__.py", bs_binja_dir / "__init__.py")
        self.link_or_copy(self.stub_files / "plugin.json", bs_binja_dir / "plugin.json")
        return path
