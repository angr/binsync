# ----------------------------------------------------------------------------
# This file contains the BinSyncController class which acts as the the
# bridge between the plugin UI and direct calls to the binsync client found in
# the core of binsync. In the controller, you will find code used to make
# pushes and pulls of user changes.
#
# You will also notice that the BinSyncController runs two extra threads in
# it:
#   1. BinSync "git pulling" thread to constantly get changes from others
#   2. Command Routine to get hooked changes to IDA attributes
#
# The second point is more complicated because it acts as the queue of
# runnable actions that are queued from inside the hooks.py file.
# Essentially, every change that happens in IDA from the main user triggers
# a hook which will push an action to be preformed onto the command queue;
# Causing a "git push" on every change.
#
# ----------------------------------------------------------------------------

from functools import wraps
import re
import threading
import time
import datetime
import logging
from typing import Dict, List, Tuple, Optional
from collections import OrderedDict, defaultdict

import idc
import idaapi
import idautils
import ida_struct
import ida_hexrays
import ida_funcs
import ida_kernwin

import binsync
from binsync.common.controller import BinSyncController, make_state, make_ro_state, init_checker, make_state_with_func
from binsync import Client, ConnectionWarnings
from binsync.data import StackVariable, StackOffsetType, Function, FunctionHeader, Struct, Comment
from . import compat

_l = logging.getLogger(name=__name__)


def update_on_view(f):
    wraps(f)
    def _update_on_view(self: IDABinSyncController, func_addr, *args, **kwargs):
        # always execute something we are looking at
        active_ctx = self.active_context()
        if active_ctx and active_ctx.addr == func_addr:
            return f(self, func_addr, *args, **kwargs)

        # otherwise, execute next time we look at it through the hooks
        task = UpdateTask(f, self, func_addr, *args, **kwargs)
        self.update_states[func_addr].add_update_task(task)

    return _update_on_view()


#
#   Wrapper Classes
#


class UpdateTask:
    def __init__(self, func, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def __eq__(self, other):
        return (
            isinstance(other, UpdateTask)
            and other.func == other.func
            and other.kwargs == other.kwargs
        )

    def __hash__(self):
        expanded_kwargs = list()
        for k, v in self.kwargs.items():
            expanded_kwargs.append(f"{k}={v}")
        return hash((self.func, *self.args, *expanded_kwargs))

    def dump(self):
        return self.func, self.args, self.kwargs


class UpdateTaskState:
    def __init__(self):
        self.update_tasks: Dict[UpdateTask, bool] = OrderedDict()
        self.update_tasks_lock = threading.Lock()

    def toggle_auto_sync_task(self, update_task):
        with self.update_tasks_lock:
            # delete the task if it is an auto_sync task already
            if update_task in list(self.update_tasks.keys()) and self.update_tasks[update_task]:
                del self.update_tasks[update_task]
                return False

            # set/make the task if its not auto_sync already
            self.update_tasks[update_task] = True
            return True

    def add_update_task(self, update_task):
        with self.update_tasks_lock:
            self.update_tasks[update_task] = False

    def do_updates(self):
        with self.update_tasks_lock:
            # run each task in the update task queue
            for update_task in list(self.update_tasks.keys()):
                f, args, kwargs = update_task.dump()
                auto_sync = self.update_tasks[update_task]

                # doing task
                try:
                    f(*args, **kwargs)
                except Exception:
                    print(f"[BinSync]: failed to execute cache of {f} with {args}")

                # remove the task if its not an auto_sync task
                if not auto_sync:
                    del self.update_tasks[update_task]


#
#   Controller
#

class IDABinSyncController(BinSyncController):
    def __init__(self):
        super(IDABinSyncController, self).__init__()

        # view change callback
        self._updated_ctx = None

        # update state for only updating when needed
        # api locks
        self.api_lock = threading.Lock()
        self.api_count = 0
        self.update_states = defaultdict(UpdateTaskState)

    #
    #   Multithreaded locks and setters
    #

    def inc_api_count(self):
        with self.api_lock:
            self.api_count += 1

    def reset_api_count(self):
        with self.api_lock:
            self.api_count = 0

    def make_controller_cmd(self, cmd_func, *args, **kwargs):
        with self.queue_lock:
            if cmd_func == self.push_struct:
                self.cmd_queue[args[0].name] = (cmd_func, args, kwargs)
            else:
                self.cmd_queue[time.time()] = (cmd_func, args, kwargs)

    #
    # Controller Interaction
    #

    def binary_hash(self) -> str:
        return idc.retrieve_input_file_md5().hex()

    def active_context(self):
        return self._updated_ctx

    def update_active_context(self, addr):
        if not addr or addr == idaapi.BADADDR:
            return

        func_addr = compat.ida_func_addr(addr)
        if func_addr is None:
            return

        func = binsync.data.Function(
            func_addr, 0, header=FunctionHeader(compat.get_func_name(func_addr), func_addr)
        )
        self._updated_ctx = func

    def binary_path(self) -> Optional[str]:
        return compat.get_binary_path()

    def get_func_size(self, func_addr) -> int:
        return compat.get_func_size(func_addr)

    #
    # IDA DataBase Fillers
    #

    @init_checker
    @make_ro_state
    def fill_structs(self, user=None, state=None):
        """
        Grab all the structs from a specified user, then fill them locally

        @param user:
        @param state:
        @return:
        """
        data_changed = False
        # sanity check, the desired user has some structs to sync
        pulled_structs: List[Struct] = self.pull_structs(user=user, state=state)
        if len(pulled_structs) <= 0:
            print(f"[BinSync]: User {user} has no structs to sync!")
            return 0

        # convert each binsync struct into an ida struct and set it in the GUI
        for struct in pulled_structs:
            data_changed |= compat.set_ida_struct(struct, self)

        # set the type of each member in the structs
        for struct in pulled_structs:
            data_changed |= compat.set_ida_struct_member_types(struct, self)

        return data_changed

    @init_checker
    @make_ro_state
    def fill_function(self, func_addr, user=None, state=None):
        """
        Grab all relevant information from the specified user and fill the @func_adrr.
        """
        data_changed = False

        # sanity check this function
        ida_func = ida_funcs.get_func(func_addr)
        if ida_func is None:
            print(f"[BinSync]: IDA Error on sync for \'{user}\' on function {hex(func_addr)}.")
            return data_changed

        #
        # FUNCTION HEADER
        #

        # function should exist in pulled state
        binsync_func = self.pull_function(func_addr, user=user, state=state) # type: Function
        if binsync_func is None:
            return data_changed

        ida_code_view = ida_hexrays.open_pseudocode(ida_func.start_ea, 0)
        # check if a header has been set for the func
        if binsync_func.header:
            updated_header = False
            try:
                updated_header = compat.set_func_header(ida_code_view, binsync_func.header, self)
            except Exception as e:
                _l.info(f"Header filling failed with exception {e}")
                self.reset_api_count()

            # if it failed, we likely are missing a type. Try again!
            if not updated_header:
                _l.info("ATTEMPTING TO SYNC STRUCTS")
                data_changed |= self.fill_structs(user=user, state=state)
            try:
                updated_header = compat.set_func_header(ida_code_view, binsync_func.header, self)
            except Exception as e:
                _l.info(f"Header filling failed with exception {e}")
                self.reset_api_count()

            data_changed |= updated_header

        #
        # COMMENTS
        #

        sync_cmts = self.pull_func_comments(func_addr, user=user, state=state)
        for addr, cmt in sync_cmts.items():
            self.inc_api_count()
            res = compat.set_ida_comment(addr, cmt.comment, decompiled=cmt.decompiled)
            if not res:
                _l.debug(f"Failed to sync comment at <{hex(addr)}>: \'{cmt.comment}\'")
                self.reset_api_count()

        #
        # STACK VARIABLES
        #

        frame = idaapi.get_frame(ida_func.start_ea)
        if frame is None or frame.memqty <= 0:
            _l.debug("Function %#x does not have an associated function frame. Skip variable name sync-up.",
                     ida_func.start_ea)
            return data_changed

        # collect and covert the info of each stack variable
        existing_stack_vars = {}
        for offset, ida_var in compat.get_func_stack_var_info(ida_func.start_ea).items():
            existing_stack_vars[compat.ida_to_angr_stack_offset(ida_func.start_ea, offset)] = ida_var

        stack_vars_to_set = {}
        # only try to set stack vars that actually exist
        for offset, stack_var in self.pull_stack_variables(func_addr, user=user, state=state).items():
            if offset in existing_stack_vars:
                # change the variable's name
                if stack_var.name != existing_stack_vars[offset].name:
                    self.inc_api_count()
                    if ida_struct.set_member_name(frame, existing_stack_vars[offset].offset, stack_var.name):
                        data_changed |= True

                # check if the variables type should be changed
                if ida_code_view and stack_var.type != existing_stack_vars[offset].type_str:
                    # validate the type is convertible
                    ida_type = compat.convert_type_str_to_ida_type(stack_var.type)
                    if ida_type is None:
                        # its possible the type is just a custom type from the same user
                        # TODO: make it possible to sync a single struct
                        if self._typestr_in_state_structs(stack_var.type, user=user, state=state):
                            data_changed |= self.fill_structs(user=user, state=state)

                        ida_type = compat.convert_type_str_to_ida_type(stack_var.type)
                        # it really is just a bad type
                        if ida_type is None:
                            _l.debug(f"Failed to parse stack variable stored type at offset"
                                     f" {hex(existing_stack_vars[offset].offset)} with type {stack_var.type}"
                                     f" on function {hex(ida_func.start_ea)}.")
                            continue

                    # queue up the change!
                    stack_vars_to_set[existing_stack_vars[offset].offset] = ida_type

            # change the type of all vars that need to be changed
            # NOTE: api_count is incremented inside the function
            data_changed |= compat.set_stack_vars_types(stack_vars_to_set, ida_code_view, self)

        # ===== update the pseudocode ==== #
        compat.refresh_pseudocode_view(binsync_func.addr)
        if data_changed:
            _l.info(f"New data synced for \'{user}\' on function {hex(ida_func.start_ea)}.")
        else:
            _l.info(f"No new data was set either by failure or lack of differences.")

    #
    #   Pullers
    #

    @init_checker
    def sync_all(self, user=None, state=None):
        # copy the actual state from the other user
        self.client.sync_states(user=user)
        new_state = self.client.get_state(user=self.client.master_user)
        func_addrs = new_state.functions.keys()
        print("[BinSync]: Target Addrs for sync being cached:", [hex(x) for x in func_addrs])

        # set the new stuff in the UI
        for func_addr in func_addrs:
            if func_addr:
                try:
                    target_func = new_state.get_function(func_addr)
                    remote_name = target_func.name

                    if remote_name != "" and remote_name:
                        compat.set_ida_func_name(func_addr, remote_name)
                except Exception:
                    pass

            update_task = UpdateTask(
                self.fill_function,
                func_addr, user=self.client.master_user
            )
            self.update_states[func_addr].add_update_task(update_task)

    #
    #   Pushers
    #

    @init_checker
    @make_state_with_func
    def push_comment(self, addr, comment, decompiled=False, func_addr=None, user=None, state=None, api_set=False):
        sync_cmt = binsync.data.Comment(addr, comment, decompiled=decompiled)
        state.set_comment(sync_cmt, set_last_change=not api_set)

    @init_checker
    @make_state_with_func
    def push_comments(self, cmt_list: List[Comment], func_addr=None, user=None, state=None, api_set=False):
        for cmt in cmt_list:
            state.set_comment(cmt)

    '''
    # TODO: Just pass along the offset. Why the whole patch ??
    @init_checker
    @make_state
    def push_patch(self, patch, user=None, state=None, api_set=False):
        # Update last pushed values
        push_time = int(time.time())
        last_push_func = compat.ida_func_addr(patch.offset)
        func_name = compat.get_func_name(last_push_func)

        state.set_patch(patch.offset, patch)
        self.client.set_last_push(last_push_func, push_time, func_name)
    '''

    @init_checker
    @make_state_with_func
    def push_function_header(self, addr, new_name, ret_type=None, args=None, user=None, state=None, api_set=False):
        func_header = FunctionHeader(new_name, addr, ret_type=ret_type, args=args)
        state.set_function_header(func_header, set_last_change=not api_set)

    @init_checker
    @make_state_with_func
    def push_stack_variable(self, addr, stack_offset, name, type_str, size, user=None, state=None, api_set=False):
        # convert longs to ints
        stack_offset = int(stack_offset)
        func_addr = int(addr)
        size = int(size)

        v = StackVariable(stack_offset,
                          StackOffsetType.IDA,
                          name,
                          type_str,
                          size,
                          func_addr)
        state.set_stack_variable(v, stack_offset, func_addr, set_last_change=not api_set)

    @init_checker
    @make_state
    def push_struct(self, struct, old_name,
                    user=None, state=None, api_set=False):
        old_name = None if old_name == "" else old_name
        state.set_struct(struct, old_name, set_last_change=not api_set)

    #
    # Utils
    #

    def _update_function_name_if_none(self, func_addr, state=None, user=None):
        curr_name = compat.get_func_name(func_addr)
        if state.functions[func_addr].name is None or state.functions[func_addr].name == "":
            state.functions[func_addr].name = curr_name
            state.save()

    @init_checker
    def _typestr_in_state_structs(self, type_str, user=None, state=None):
        binsync_structs = state.get_structs()
        for struct in binsync_structs:
            if struct.name in type_str:
                return True

        return False
