from typing import Optional
from collections import OrderedDict
import threading
import datetime
import time
import os

from PySide2.QtWidgets import QMessageBox

from angrmanagement.ui.views import CodeView
from angr.analyses.decompiler.structured_codegen import DummyStructuredCodeGenerator
from angr.knowledge_plugins.sync.sync_controller import SyncController
from angr import knowledge_plugins
import angr

from binsync.common.controller import *
from binsync.data import StackOffsetType
import binsync


class AngrBinSyncController(BinSyncController):
    """
    The class used for all pushing/pulling and merging based actions with BinSync data.
    This class is resposible for handling callbacks that are done by changes from the local user
    and responsible for running a thread to get new changes from other users.
    """

    def __init__(self, workspace):
        super(AngrBinSyncController, self).__init__()
        self._workspace = workspace
        self._instance = workspace.instance

    def get_binary_hash(self) -> str:
        return self._instance.project.loader.main_object.md5.hex()

    #
    # Display Fillers
    #

    @init_checker
    @make_ro_state
    def fill_function(self, func_addr, user=None, state=None):
        func = self._instance.kb.functions[func_addr]

        # re-decompile a function if needed
        decompilation = self.decompile_function(func)

        _func: binsync.data.Function = self.pull_function(func.addr, user=user)
        if _func is None:
            # the function does not exist for that user's state
            return False

        # ==== Function Name ==== #
        func.name = _func.name
        decompilation.cfunc.name = _func.name
        decompilation.cfunc.demangled_name = _func.name

        # ==== Comments ==== #
        sync_cmts = self.pull_comments(func.addr, user=user)
        for addr, cmt in sync_cmts.items():
            if cmt.comment:
                if cmt.decompiled:
                    pos = decompilation.map_addr_to_pos.get_nearest_pos(addr)
                    corrected_addr = decompilation.map_pos_to_addr.get_node(pos).tags['ins_addr']
                    decompilation.stmt_comments[corrected_addr] = cmt.comment
                else:
                    self._instance.kb.comments[cmt.addr] = cmt.comment

        # ==== Stack Vars ==== #
        sync_vars = self.pull_stack_variables(func.addr, user=user)
        for offset, sync_var in sync_vars.items():
            code_var = AngrBinSyncController.find_stack_var_in_codegen(decompilation, offset)
            if code_var:
                code_var.name = sync_var.name
                code_var.renamed = True

        decompilation.regenerate_text()
        self.decompile_function(func, refresh_gui=True)

    #
    #   Pusher Alias
    #

    @init_checker
    @make_state
    def push_stack_variable(self, func_addr, offset, name, type_, size_, user=None, state=None):
        sync_var = binsync.data.StackVariable(offset, StackOffsetType.ANGR, name, type_, size_, func_addr)
        return state.set_stack_variable(sync_var, offset, func_addr)

    @init_checker
    @make_state
    def push_comment(self, addr, cmt, decompiled, user=None, state=None):
        func_addr = self._get_func_addr_from_addr(addr)
        sync_cmt = binsync.data.Comment(func_addr, addr, comment, decompiled=decompiled)
        return state.set_comment(sync_cmt)

    @init_checker
    @make_state
    def push_func(self, func: knowledge_plugins.functions.Function, user=None, state=None):
        _func = binsync.data.Function(func.addr, name=func.name)
        return state.set_function(_func)

    #
    #   Utils
    #

    def decompile_function(self, func, refresh_gui=False):
        # check for known decompilation
        available = self._instance.kb.structured_code.available_flavors(func.addr)
        should_decompile = False
        if 'pseudocode' not in available:
            should_decompile = True
        else:
            cached = self._instance.kb.structured_code[(func.addr, 'pseudocode')]
            if isinstance(cached, DummyStructuredCodeGenerator):
                should_decompile = True

        if should_decompile:
            # recover direct pseudocode
            self._instance.project.analyses.Decompiler(func, flavor='pseudocode')

            # attempt to get source code if its available
            source_root = None
            if self._instance.original_binary_path:
                source_root = os.path.dirname(self._instance.original_binary_path)
            self._instance.project.analyses.ImportSourceCode(func, flavor='source', source_root=source_root)

        # grab newly cached pseudocode
        decomp = self._instance.kb.structured_code[(func.addr, 'pseudocode')].codegen
        if refresh_gui:
            # refresh all views
            self._workspace.reload()

            # re-decompile current view to cause a refresh
            current_tab = self._workspace.view_manager.current_tab
            if isinstance(current_tab, CodeView) and current_tab.function == func:
                self._workspace.decompile_current_function()

        return decomp

    @staticmethod
    def find_stack_var_in_codegen(decompilation, stack_offset: int) -> Optional[angr.sim_variable.SimStackVariable]:
        for var in decompilation.cfunc.variable_manager._unified_variables:
            if hasattr(var, "offset") and var.offset == stack_offset:
                return var

        return None

    def _get_func_addr_from_addr(self, addr):
        try:
            func_addr = self._kb.cfgs.get_most_accurate().get_any_node(addr, anyaddr=True).function_address
        except AttributeError:
            func_addr = -1

        return func_addr