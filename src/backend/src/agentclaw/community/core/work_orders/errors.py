"""Leaf failures exposed by the work-order Service API."""


class WorkOrderError(RuntimeError):
    """Base class for work-order failures."""


class WorkOrderNotFoundError(WorkOrderError):
    pass


class WorkOrderNotificationNotFoundError(WorkOrderError):
    pass


class WorkOrderAccessDeniedError(WorkOrderError):
    pass


class WorkOrderInvalidReasonError(WorkOrderError):
    pass


class WorkOrderInvalidRemarkError(WorkOrderError):
    pass


class WorkOrderAlreadyPendingError(WorkOrderError):
    pass


class WorkOrderAlreadyProcessedError(WorkOrderError):
    pass


class WorkOrderApplicantAlreadyMemberError(WorkOrderError):
    pass


class WorkOrderApplicantAlreadyEditorError(WorkOrderError):
    pass


class WorkOrderJoinNotAllowedError(WorkOrderError):
    pass


class WorkOrderBotEditorRequestNotAllowedError(WorkOrderError):
    pass


class WorkOrderNoReviewerError(WorkOrderError):
    pass


class WorkOrderInvalidEventError(WorkOrderError):
    pass
