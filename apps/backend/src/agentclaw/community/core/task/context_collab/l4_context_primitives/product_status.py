"""Closed product lifecycle vocabulary; stop is a separate persisted fact."""

from enum import Enum


class StatusEnum(str, Enum):
    CREATE = "CREATE"
    RUNNING = "RUNNING"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_FAILED = "RUN_FAILED"
    ACCEPTANCE_SUCCEED = "ACCEPTANCE_SUCCEED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"


class EventTypeEnum(str, Enum):
    RUN_FAILED = "RUN_FAILED"
    ACCEPTANCE_SUCCEED = "ACCEPTANCE_SUCCEED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"


class AdjustTypeEnum(str, Enum):
    DO_NOTHING = "DO_NOTHING"
    OPTIMIZE = "OPTIMIZE"
    RE_DO = "RE_DO"
    SHUTDOWN = "SHUTDOWN"
