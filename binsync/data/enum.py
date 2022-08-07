from collections import OrderedDict
from typing import Dict

import toml

from binsync.data.artifact import Artifact


class Enum(Artifact):
    __slots__ = Artifact.__slots__ + (
        "name",
        "members",
    )

    def __init__(self, name, members: Dict[str, int], last_change=None):
        super(Enum, self).__init__(last_change=last_change)
        self.name = name
        # sorts map by the int value
        self.members = OrderedDict(sorted(members.items(), key=lambda kv: kv[1]))

    def __str__(self):
        return f"<Enum: {self.name} values={len(self.members)}>"

    def __repr__(self):
        return self.__str__()

    @classmethod
    def parse(cls, s):
        en = Enum(None, {})
        en.__setstate__(toml.loads(s))
        return en

    @classmethod
    def load_many(cls, enums_toml):
        for enum_toml in enums_toml.values():
            enum = Enum(None, {})
            try:
                enum.__setstate__(enum_toml)
            except TypeError:
                # skip all incorrect ones
                continue
            yield enum

    @classmethod
    def dump_many(cls, enums):
        enums_ = {}

        for name, enum in enums.items():
            enums_[name] = enum.__getstate__()
        return enums_

    def copy(self):
        return Enum(
            self.name,
            self.members.copy(),
            last_change=self.last_change
        )

    @classmethod
    def from_nonconflicting_merge(cls, enum1: "Enum", enum2: "Enum"):
        if not enum2 or enum1 == enum2:
            return enum1.copy()

