import logging
import threading
import time
from collections import OrderedDict, defaultdict
from functools import wraps
from typing import Dict, Iterable, List, Optional, Union

import binsync.data
from binsync.common.artifact_lifter import ArtifactLifter
from binsync.core.client import Client, SchedSpeed
from binsync.data import (
    State, User, Artifact,
    Function, FunctionHeader, FunctionArgument, StackVariable,
    Comment, GlobalVariable, Patch,
    Enum, Struct
)

_l = logging.getLogger(name=__name__)


#
# State Checking Decorators
#

def init_checker(f):
    @wraps(f)
    def _init_check(self, *args, **kwargs):
        if not self.check_client():
            raise RuntimeError("Please connect to a repo first.")
        return f(self, *args, **kwargs)

    return _init_check


def make_ro_state(f):
    """
    Build a read-only State _instance and pass to `f` as the `state` kwarg if the `state` kwarg is None.
    Function `f` should have have at least two kwargs, `user` and `state`.
    """

    @wraps(f)
    def state_check(self, *args, **kwargs):
        state = kwargs.pop('state', None)
        user = kwargs.pop('user', None)
        if state is None:
            state = self.client.get_state(user=user)
        kwargs['state'] = state
        kwargs['user'] = user
        return f(self, *args, **kwargs)

    return state_check


def check_sync_logs(f):
    """
    Check a log of last sync times for all synced artifacts. If the most recent sync time for artifact is greater than
    the most recent edit time, do not continue with sync.
    """

    sync_map = {
        BinSyncController.fill_function.__name__: (Function, False),
        BinSyncController.fill_functions.__name__: (Function, True),
        BinSyncController.fill_global_var.__name__: (GlobalVariable, False),
        BinSyncController.fill_global_vars.__name__: (GlobalVariable, True),
        BinSyncController.fill_struct.__name__: (Struct, False),
        BinSyncController.fill_structs.__name__: (Struct, True),
        BinSyncController.fill_enum.__name__: (Enum, False),
        BinSyncController.fill_enums.__name__: (Enum, True)
    }

    # Note: In its current state this should only be applied as a decorator to
    # fill_x functions not fill_xs
    @wraps(f)
    def sync_check(self, *args, **kwargs):
        user_state = kwargs['state']
        type_, many = sync_map[f.__name__]
        identifiers = args
        user_artifact: Artifact = self.pull_artifact(type_, *identifiers, many=many, state=user_state)

        master_state = self.client.get_state()
        master_artifact: Artifact = self.pull_artifact(type_, *identifiers, many=many, state=master_state)
        if master_artifact == user_artifact:
            return False

        # args and kwargs are left the same for singleton fill functions, we checked time, and it
        # needs an update. We theoretically could pass in the binsync artifact that we loaded already but
        # the time save is likely nominal (or even entirely killed by other side effects)
        return f(self, *args, **kwargs)

    return sync_check

#
# Description Constants
#

# https://stackoverflow.com/questions/10926328
BUSY_LOOP_COOLDOWN = 0.5
GET_MANY = True


class SyncControlStatus:
    CONNECTED = 0
    CONNECTED_NO_REMOTE = 1
    DISCONNECTED = 2


class SyncLevel:
    OVERWRITE = 0
    NON_CONFLICTING = 1
    MERGE = 2


#
#   Controller
#

class BinSyncController:

    ARTIFACT_SET_MAP = {
        Function: State.set_function,
        FunctionHeader: State.set_function_header,
        StackVariable: State.set_stack_variable,
        Comment: State.set_comment,
        GlobalVariable: State.set_global_var,
        Struct: State.set_struct,
        Enum: State.set_enum
    }

    ARTIFACT_GET_MAP = {
        Function: State.get_function,
        (Function, GET_MANY): State.get_functions,
        StackVariable: State.get_stack_variable,
        (StackVariable, GET_MANY): State.get_stack_variables,
        Comment: State.get_comment,
        (Comment, GET_MANY): State.get_func_comments,
        GlobalVariable: State.get_global_var,
        (GlobalVariable, GET_MANY): State.get_global_vars,
        Struct: State.get_struct,
        (Struct, GET_MANY): State.get_structs,
        Enum: State.get_enum,
        (Enum, GET_MANY): State.get_enums,
    }

    """
    The BinSync Controller is the main interface for syncing with the BinSync Client which preforms git tasks
    such as pull and push. In the Controller higher-level tasks are done such as updating UI with changes
    and preforming syncs and pushes on data users need/change.

    All class properties that have a "= None" means they must be set during runtime by an outside process.
    The client will be set on connection. The ctx_change_callback will be set by an outside UI

    """
    def __init__(self, artifact_lifter, headless=False, reload_time=10):
        self.headless = headless
        self.reload_time = reload_time
        self.artifact_lifer: ArtifactLifter = artifact_lifter

        # client created on connection
        self.client = None  # type: Optional[Client]

        # ui callback created on UI init
        self.ui_callback = None  # func()
        self.ctx_change_callback = None  # func()
        self._last_reload = None
        self.last_ctx = None

        # settings
        self.sync_level: int = SyncLevel.NON_CONFLICTING

        # command locks
        self.queue_lock = threading.Lock()
        self.cmd_queue = OrderedDict()

        # create a pulling thread, but start on connection
        self.updater_thread = threading.Thread(target=self.updater_routine)
    #
    #   Multithreading updaters, locks, and evaluators
    #

    def make_controller_cmd(self, cmd_func, *args, **kwargs):
        with self.queue_lock:
            self.cmd_queue[time.time()] = (cmd_func, args, kwargs)

    def _eval_cmd(self, cmd):
        # parse the command if present
        if not cmd:
            return

        func, f_args, f_kargs = cmd[:]
        func(*f_args, **f_kargs)

    def _eval_cmd_queue(self):
        with self.queue_lock:
            if not self.cmd_queue:
                return

            job_count = 1
            jobs = [
                self.cmd_queue.popitem(last=False)[1] for _ in range(job_count)
            ]

        for job in jobs:
            self._eval_cmd(job)

    def updater_routine(self):
        while True:
            time.sleep(BUSY_LOOP_COOLDOWN)

            # validate a client is connected to this controller (may not have remote )
            if not self.check_client():
                continue

            # do git pull/push operations if a remote exist for the client
            if self.client.has_remote:
                if self.client.last_pull_attempt_ts is None:
                    self.client.update(commit_msg="User created")

                # update every reload_time
                elif time.time() - self.client.last_pull_attempt_ts > self.reload_time:
                    self.client.update()

            if not self.headless:
                # update context knowledge every loop iteration
                if self.ctx_change_callback:
                    self._check_and_notify_ctx()

                # update the control panel with new info every BINSYNC_RELOAD_TIME seconds
                if self._last_reload is None or \
                        time.time() - self._last_reload > self.reload_time:
                    self._last_reload = time.time()
                    self._update_ui()

            # evaluate commands started by the user
            self._eval_cmd_queue()

    def _update_ui(self):
        if not self.ui_callback:
            return

        self.ui_callback()

    def start_updater_routine(self):
        self.updater_thread.setDaemon(True)
        self.updater_thread.start()

    def _check_and_notify_ctx(self):
        active_ctx = self.active_context()
        if active_ctx is None or self.last_ctx == active_ctx:
            return

        self.last_ctx = active_ctx
        self.ctx_change_callback()

    #
    # Client Interaction Functions
    #

    def connect(self, user, path, init_repo=False, remote_url=None):
        binary_hash = self.binary_hash()
        self.client = Client(
            user, path, binary_hash, init_repo=init_repo, remote_url=remote_url
        )

        self.start_updater_routine()
        return self.client.connection_warnings

    def check_client(self):
        return self.client is not None

    def status(self):
        if self.check_client():
            if self.client.has_remote and self.client.active_remote:
                return SyncControlStatus.CONNECTED
            return SyncControlStatus.CONNECTED_NO_REMOTE
        return SyncControlStatus.DISCONNECTED

    def status_string(self):
        stat = self.status()
        if stat == SyncControlStatus.CONNECTED:
            return f"<font color=#1eba06>{self.client.master_user}</font>"
        elif stat == SyncControlStatus.CONNECTED_NO_REMOTE:
            return f"<font color=#e7b416>{self.client.master_user}</font>"
        else:
            return "<font color=#cc3232>Disconnected</font>"

    def toggle_headless(self):
        self.headless = not self.headless

    @init_checker
    def users(self, priority=None) -> Iterable[User]:
        return self.client.users(priority=priority)

    def usernames(self, priority=None) -> Iterable[str]:
        for user in self.users(priority=priority):
            yield user.name

    #
    # Override Mandatory API:
    # These functions create a public API for things that hold a reference to the Controller from either another
    # thread or object. This is most useful for use in the UI, which can use this API to make general requests from
    # the decompiler regardless of internal decompiler API.
    #

    def binary_hash(self) -> str:
        """
        Returns a hex string of the currently loaded binary in the decompiler. For most cases,
        this will simply be a md5hash of the binary.

        @rtype: hex string
        """
        raise NotImplementedError

    def active_context(self) -> binsync.data.Function:
        """
        Returns an binsync Function. Currently only functions are supported as current contexts.
        This function will be called very frequently, so its important that its implementation is fast
        and can be done many times in the decompiler.
        """
        raise NotImplementedError

    def binary_path(self) -> Optional[str]:
        """
        Returns a string that is the path of the currently loaded binary. If there is no binary loaded
        then None should be returned.

        @rtype: path-like string (/path/to/binary)
        """
        raise NotImplementedError

    def get_func_size(self, func_addr) -> int:
        """
        Returns the size of a function

        @param func_addr:
        @return:
        """
        raise NotImplementedError

    def goto_address(self, func_addr) -> None:
        """
        Relocates decompiler display to provided address

        @param func_addr:
        @return:
        """
        raise NotImplementedError

    #
    # Optional Artifact API:
    # A series of functions that allow public access to live artifacts in the decompiler. As an example,
    # `function(addr)` will return the current Function at addr that the user would be seeing. This is useful
    # for having a common interface of reading data from other decompilers.
    #

    def functions(self) -> Dict[int, Function]:
        """
        Returns a dict of binsync.Functions that contain the addr, name, and size of each function in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    def function(self, addr) -> Optional[Function]:
        return None

    def global_vars(self) -> Dict[int, GlobalVariable]:
        """
        Returns a dict of binsync.GlobalVariable that contain the addr and size of each global var.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    def global_var(self, addr) -> Optional[GlobalVariable]:
        return None

    def structs(self) -> Dict[str, Struct]:
        """
        Returns a dict of binsync.Structs that contain the name and size of each struct in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    def struct(self, name) -> Optional[Struct]:
        return None

    def enums(self) -> Dict[str, Enum]:
        """
        Returns a dict of binsync.Enum that contain the name of the enums in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    def enum(self, name) -> Optional[Enum]:
        return None

    def patches(self) -> Dict[int, Patch]:
        """
        Returns a dict of binsync.Patch that contain the addr of each Patch and the bytes.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    def patch(self, addr) -> Optional[Patch]:
        return None

    def global_artifacts(self):
        """
        Returns a light version of all artifacts that are global (non function associated):
        - structs, gvars, enums

        @return:
        """
        g_artifacts = {}
        for f in [self.structs, self.global_vars, self.enums]:
            g_artifacts.update(f())

        return g_artifacts

    def global_artifact(self, lookup_item: Union[str, int]):
        """
        Returns a live binsync.data version of the Artifact located at the lookup_item location, which can
        lookup any artifact supported in `global_artifacts`

        @param lookup_item:
        @return:
        """

        if isinstance(lookup_item, int):
            return self.global_var(lookup_item)
        elif isinstance(lookup_item, str):
            artifact = self.struct(lookup_item)
            if artifact:
                return artifact

            artifact = self.enum(lookup_item)
            return artifact

        return None

    #
    # Client API & Shortcuts
    #

    def lift_artifact(self, artifact: Artifact) -> Artifact:
        return self.artifact_lifer.lift(artifact)

    def lower_artifact(self, artifact: Artifact) -> Artifact:
        return self.artifact_lifer.lower(artifact)

    def get_state(self, user=None, version=None, priority=None, no_cache=False) -> State:
        return self.client.get_state(user=user, version=version, priority=priority, no_cache=no_cache)

    @init_checker
    def pull_artifact(self, type_: Artifact, *identifiers, many=False, user=None, state=None) -> Optional[Artifact]:
        try:
            get_artifact_func = self.ARTIFACT_GET_MAP[type_] if not many else self.ARTIFACT_GET_MAP[(type_, GET_MANY)]
        except KeyError:
            _l.info(f"Attempting to pull an unsupported Artifact of type {type_} with {identifiers}")
            return None

        # assure a state exists
        if not state:
            state = self.get_state(user=user)

        try:
            artifact = get_artifact_func(state, *identifiers)
        except Exception:
            _l.warning(f"Failed to pull an supported Artifact of type {type_} with {identifiers}")
            return None

        if not artifact:
            return artifact

        return self.lower_artifact(artifact)

    def push_artifact(self, artifact: Artifact, user=None, state=None, commit_msg=None, api_set=False) -> bool:
        """
        Every pusher artifact does three things
        1. Get the state setter function based on the class of the Obj
        2. Get the commit msg of the obj based on the class
        3. Lift the obj based on the Controller lifters

        @param artifact:
        @param user:
        @param state:
        @return:
        """
        if not artifact:
            return False

        try:
            set_artifact_func = self.ARTIFACT_SET_MAP[artifact.__class__]
        except KeyError:
            _l.info(f"Attempting to push an unsupported Artifact of type {artifact}")
            return False

        # assure state exists
        if not state:
            state = self.get_state(user=user)

        # assure function existence for artifacts requiring a function
        if isinstance(artifact, (FunctionHeader, StackVariable, Comment)):
            func_addr = artifact.func_addr if hasattr(artifact, "func_addr") else artifact.addr
            if func_addr:
                self.push_artifact(Function(func_addr, self.get_func_size(func_addr)), state=state)

        # lift artifact into standard BinSync format
        artifact = self.lift_artifact(artifact)

        # set the artifact in the target state, likely master
        was_set = set_artifact_func(state, artifact, set_last_change=not api_set)
        if was_set:
            self.client.commit_state(state, msg=commit_msg or artifact.commit_msg)

        return was_set

    #
    # Fillers:
    # A filler function is generally responsible for pulling down data from a specific user state
    # and reflecting those changes in decompiler view (like the text on the screen). Normally, these changes
    # will also be accompanied by a Git commit to the master users state to save the changes from pull and
    # fill into their BS database. In special cases, a filler may only update the decompiler UI but not directly
    # cause a save of the BS state.
    #

    @init_checker
    @make_ro_state
    def fill_struct(self, struct_name, user=None, state=None, header=True, members=True):
        """

        @param struct_name:
        @param user:
        @param state:
        @param header:
        @param members:
        @return:
        """
        _l.info(f"Fill Struct is not implemented in your decompiler.")
        return False

    @init_checker
    @make_ro_state
    def fill_structs(self, user=None, state=None):
        """
        Grab all the structs from a specified user, then fill them locally

        @param user:
        @param state:
        @return:
        """
        changes = False
        # only do struct headers for circular references
        for name, struct in state.structs.items():
            changes |= self.fill_struct(name, user=user, state=state, members=False)

        for name, struct in state.structs.items():
            changes |= self.fill_struct(name, user=user, state=state, header=False)

        return changes

    @init_checker
    @make_ro_state
    def fill_global_var(self, var_addr, user=None, state=None):
        """
        Grab a global variable for a specified address and fill it locally

        @param var_addr:
        @param user:
        @param state:
        @return:
        """
        _l.info(f"Fill Global Var is not implemented in your decompiler.")
        return False

    @init_checker
    @make_ro_state
    def fill_global_vars(self, user=None, state=None):
        changes = False
        for off, gvar in state.global_vars.items():
            changes |= self.fill_global_var(off, user=user, state=state)

        return changes

    @init_checker
    @make_ro_state
    def fill_enum(self, enum_name, user=None, state=None):
        """
        Grab an enum and fill it locally

        @param enum_name:
        @param user:
        @param state:
        @return:
        """
        _l.info(f"Fill Enum is not implemented in your decompiler.")
        return False

    @init_checker
    @make_ro_state
    def fill_enums(self, user=None, state=None):
        """
        Grab all enums and fill it locally

        @param user:
        @param state:
        @return:
        """
        changes = False
        for name, enum in state.enums.items():
            changes |= self.fill_enum(name, user=user, state=state)

        return changes

    @init_checker
    @make_ro_state
    def fill_function(self, func_addr, user=None, state=None):
        """
        Grab all relevant information from the specified user and fill the @func_adrr.
        """
        _l.info(f"Fill Function is not implemented in your decompiler.")
        return False

    @init_checker
    @make_ro_state
    def fill_functions(self, user=None, state=None):
        change = False
        for addr, func in state.functions.items():
            change |= self.fill_function(addr, user=user, state=state)

        return change

    @init_checker
    @make_ro_state
    def fill_all(self, user=None, state=None):
        """
        Connected to the Sync All action:
        syncs in all the data from the targeted user

        @param user:
        @param state:
        @return:
        """
        _l.info(f"Filling all data from user {user}...")

        fillers = [
            self.fill_structs, self.fill_enums, self.fill_global_vars, self.fill_functions
        ]

        changes = False
        for filler in fillers:
            changes |= filler(user=user, state=state)

        return changes

    @init_checker
    def magic_fill(self, preference_user=None):
        """
        Traverses all the data in the BinSync repo, starting with an optional preference user,
        and sequentially merges that data together in a non-conflicting way. This also means that the prefrence
        user makes up the majority of the initial data you sync in.

        This process supports: functions (header, stack vars), structs, and global vars
        TODO:
        - support for enums
        - refactor fill_function to stop attempting to set master state after we do
        -

        @param preference_user:
        @return:
        """
        _l.info(f"Staring a Magic Sync with a preference for {preference_user}")
        # re-order users for the prefered user to be at the front of the queue (if they exist)
        all_users = list(self.usernames(priority=SchedSpeed.FAST))
        preference_user = preference_user if preference_user else self.client.master_user
        all_users.remove(preference_user)
        master_state = self.client.get_state(user=self.client.master_user, priority=SchedSpeed.FAST)

        target_artifacts = {
            Struct: self.fill_struct,
            Comment: lambda *x, **y: None,
            Function: self.fill_function,
            GlobalVariable: self.fill_global_var
        }

        for artifact_type, filler_func in target_artifacts.items():
            _l.info(f"Magic Syncing artifacts of type {artifact_type.__name__} now...")
            pref_state = self.get_state(user=preference_user, priority=SchedSpeed.FAST)
            for identifier in self.changed_artifacts_of_type(artifact_type):
                pref_art = self.pull_artifact(artifact_type, identifier, state=pref_state)
                for user in all_users:
                    user_state = self.get_state(user=user, priority=SchedSpeed.FAST)
                    user_art = self.pull_artifact(artifact_type, identifier, state=user_state)

                    if not user_art:
                        continue

                    if not pref_art:
                        pref_art = user_art.copy()

                    pref_art = artifact_type.from_nonconflicting_merge(pref_art, user_art)
                    pref_art.last_change = None

                self.push_artifact(pref_art, state=master_state, commit_msg=f"Magic Synced {pref_art}")
                filler_func(identifier, state=master_state)

        _l.info(f"Magic Syncing Completed!")

    #
    # Force Push
    #

    @init_checker
    def force_push_function(self, addr: int) -> bool:
        """
        Collects the function currently stored in the decompiler, not the BS State, and commits it to
        the master users BS Database.

        TODO: push the comments and custom types that are associated with each stack var
        TODO: refactor to use internal push_function for correct commit message

        @param addr:
        @return: Success of committing the Function
        """
        func = self.function(addr)
        if not func:
            _l.info(f"Pushing function {hex(addr)} Failed")
            return False

        master_state: State = self.client.get_state(priority=SchedSpeed.FAST)
        pushed = self.push_artifact(func, state=master_state, commit_msg=f"Forced pushed function {func}")
        return pushed


    @init_checker
    def force_push_global_artifact(self, lookup_item):
        """
        Collects the global artifact (struct, gvar, enum) currently stored in the decompiler, not the BS State,
        and commits it to the master users BS Database.

        @param lookup_item:
        @return: Success of committing the Artifact
        """
        global_art = self.global_artifact(lookup_item)
        
        if not global_art:
            _l.info(f"Pushing global artifact {lookup_item if isinstance(lookup_item, str) else hex(lookup_item)} Failed")
            return False

        master_state: State = self.client.get_state(priority=SchedSpeed.FAST)
        global_art = self.artifact_lifer.lift(global_art)
        pushed = self.push_artifact(global_art, state=master_state, commit_msg=f"Force pushed {global_art}")
        return pushed


    #
    # Utils
    #

    def generate_func_for_sync_level(self, sync_func: Function) -> Function:
        if self.sync_level == SyncLevel.OVERWRITE:
            return sync_func

        master_state = self.client.get_state()
        master_func = master_state.get_function(sync_func.addr)
        if not master_func:
            return sync_func

        if self.sync_level == SyncLevel.NON_CONFLICTING:
            new_func = Function.from_nonconflicting_merge(master_func, sync_func)

        elif self.sync_level == SyncLevel.MERGE:
            _l.warning("Manual Merging is not currently supported, using non-conflict syncing...")
            new_func = Function.from_nonconflicting_merge(master_func, sync_func)

        else:
            raise Exception("Your BinSync Client has an unsupported Sync Level activated")

        return new_func

    def changed_artifacts_of_type(self, type_: Artifact):
        prop_map = {
            Function: "functions",
            Comment: "comments",
            GlobalVariable: "global_vars",
            Struct: "structs"
        }

        try:
            prop_name = prop_map[type_]
        except KeyError:
            _l.info(f"Attempted to get changed artifacts of type {type_} which is unsupported")
            return set()

        known_arts = set()
        for username in self.usernames(priority=SchedSpeed.FAST):
            state = self.client.get_state(user=username, priority=SchedSpeed.FAST)
            artifact_dict: Dict = getattr(state, prop_name)
            for identifier in artifact_dict:
                known_arts.add(identifier)

        return known_arts
