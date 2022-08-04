import logging
import os
from typing import Optional, Dict

import angr
import binsync
from angr.analyses.decompiler.structured_codegen import DummyStructuredCodeGenerator
from angrmanagement.ui.views import CodeView

from binsync import Function
from binsync.common.controller import (
    BinSyncController,
    init_checker,
    make_ro_state,
)
from binsync.data import (
    FunctionHeader, StackOffsetType, Comment, StackVariable
)

from .artifact_lifter import AngrArtifactLifter

l = logging.getLogger(__name__)


class AngrBinSyncController(BinSyncController):
    """
    The class used for all pushing/pulling and merging based actions with BinSync data.
    This class is responsible for handling callbacks that are done by changes from the local user
    and responsible for running a thread to get new changes from other users.
    """

    def __init__(self, workspace):
        super(AngrBinSyncController, self).__init__(artifact_lifter=AngrArtifactLifter(self))
        self._workspace = workspace
        self._instance = workspace.instance

    def binary_hash(self) -> str:
        return self._instance.project.loader.main_object.md5.hex()

    def active_context(self):
        curr_view = self._workspace.view_manager.current_tab
        if not curr_view:
            return None

        try:
            func = curr_view.function
        except NotImplementedError:
            return None

        if func is None or func.am_obj is None:
            return None

        func_addr = self.rebase_addr(func.addr)

        return binsync.data.Function(
            func_addr, 0, header=FunctionHeader(func.name, func_addr)
        )

    def binary_path(self) -> Optional[str]:
        try:
            return self._instance.project.loader.main_object.binary
        # pylint: disable=broad-except
        except Exception:
            return None

    def get_func_size(self, func_addr) -> int:
        try:
            func = self._instance.kb.functions[func_addr]
            return func.size
        except KeyError:
            return 0

    def rebase_addr(self, addr, up=False):
        base_addr = self._instance.project.loader.main_object.mapped_base
        is_pie = self._instance.project.loader.main_object.pic

        if is_pie:
            if up:
                return addr + base_addr
            elif addr > base_addr:
                return addr - base_addr

        return addr

    #
    # Display Fillers
    #

    def fill_global_var(self, var_addr, user=None, state=None):
        return False

    def fill_struct(self, struct_name, user=None, state=None, header=True, members=True):
        return False

    @init_checker
    @make_ro_state
    def fill_function(self, func_addr, user=None, state=None):
        func = self._instance.kb.functions[self.artifact_lifer.lower_addr(func_addr)]

        # re-decompile a function if needed
        decompilation = self.decompile_function(func)

        # use a lifted version of stat function go generate function
        sync_func: Function = state.get_function(func_addr)
        if sync_func is None:
            # the function does not exist for that user's state
            return False

        sync_func = self.merge_function_into_master(sync_func)

        #
        # Function Header
        #

        if sync_func.header:
            if sync_func.name and sync_func.name != func.name:
                func.name = sync_func.name
                decompilation.cfunc.name = sync_func.name
                decompilation.cfunc.demangled_name = sync_func.name

            if sync_func.header.args:
                for i, arg in sync_func.header.args.items():
                    if i >= len(decompilation.cfunc.arg_list):
                        break

                    decompilation.cfunc.arg_list[i].variable.name = arg.name

        #
        # Comments
        #

        for addr, cmt in self.pull_artifact(Comment, func_addr, many=True, user=user, state=state).items():
            if not cmt or not cmt.comment:
                continue

            if cmt.decompiled:
                try:
                    pos = decompilation.map_addr_to_pos.get_nearest_pos(addr)
                    corrected_addr = decompilation.map_pos_to_addr.get_node(pos).tags['ins_addr']
                # pylint: disable=broad-except
                except Exception:
                    break

                decompilation.stmt_comments[corrected_addr] = cmt.comment
            else:
                self._instance.kb.comments[cmt.addr] = cmt.comment

        # ==== Stack Vars ==== #
        sync_vars = self.pull_artifact(StackVariable, func_addr, many=True, state=state, user=user)
        for offset, sync_var in sync_vars.items():
            code_var = AngrBinSyncController.find_stack_var_in_codegen(decompilation, offset)
            if code_var:
                code_var.name = sync_var.name
                code_var.renamed = True

        decompilation.regenerate_text()
        self.decompile_function(func, refresh_gui=True)
        return True

    #
    # Artifact
    #

    def functions(self) -> Dict[int, Function]:
        funcs = {}
        for addr, func in self._instance.kb.functions.items():
            funcs[addr] = Function(addr, func.size)
            funcs[addr].name = func.name

        return funcs

    def function(self, addr) -> Optional[Function]:
        """
        TODO: add support for stack variables and function args

        @param addr:
        @return:
        """
        try:
            _func = self._instance.kb.functions[addr]
        except KeyError:
            return None

        func = Function(_func.addr, _func.size)
        func_header = FunctionHeader(_func.name, _func.addr, ret_type=_func.prototype.c_repr())

        func.header = func_header
        return func


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

    @staticmethod
    def stack_var_type_str(decompilation, stack_var: angr.sim_variable.SimStackVariable):
        try:
            var_type = decompilation.cfunc.variable_manager.get_variable_type(stack_var)
        # pylint: disable=broad-except
        except Exception:
            return None

        return var_type.c_repr()

    @staticmethod
    def get_func_args(decompilation):
        arg_info = {
            i: (arg.variable, decompilation.cfunc.functy.args[i].c_repr())
            for i, arg in enumerate(decompilation.cfunc.arg_list)
        }
        return arg_info

    @staticmethod
    def func_insn_addrs(func: angr.knowledge_plugins.Function):
        insn_addrs = set()
        for block in func.blocks:
            insn_addrs.update(block.instruction_addrs)

        return insn_addrs

    def get_func_addr_from_addr(self, addr):
        try:
            func_addr = self._workspace.instance.kb.cfgs.get_most_accurate()\
                .get_any_node(addr, anyaddr=True)\
                .function_address
        except AttributeError:
            func_addr = None

        return func_addr
    
    def goto_address(self, func_addr):
        self._workspace.jump_to(self.rebase_addr(func_addr, up=True))
