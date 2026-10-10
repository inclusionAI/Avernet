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


class WorkOrderSkillEditorRequestNotAllowedError(WorkOrderError):
    pass


class WorkOrderSkillApplicantAlreadyEditorError(WorkOrderError):
    pass


class WorkOrderNoReviewerError(WorkOrderError):
    pass


class WorkOrderInvalidEventError(WorkOrderError):
    pass


class WorkOrderCallbackError(WorkOrderError):
    """A required upstream business callback failed or reported failure."""

    pass


class WorkOrderLocalFinalizeError(WorkOrderError):
    """Remote decision confirmed, local persistence failed or outcome is uncertain."""

    def __init__(self, work_order_id: int) -> None:
        self.work_order_id = work_order_id
        super().__init__(
            "External decision completed; local result persistence failed. "
            "Reconcile before retrying."
        )
