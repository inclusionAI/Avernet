"""Domain errors for spaces and members."""


class SpaceError(RuntimeError):
    """Base class for space-domain failures."""


class SpaceNotFoundError(SpaceError):
    pass


class SpaceAccessDeniedError(SpaceError):
    pass


class SpaceNameInvalidError(SpaceError):
    pass


class SpaceAlreadyExistsError(SpaceError):
    pass


class SpaceMemberAlreadyExistsError(SpaceError):
    pass


class SpaceMemberInvalidError(SpaceError):
    pass


class SpaceMemberNotFoundError(SpaceError):
    pass


class SpaceCreatorInvariantError(SpaceError):
    pass


class PersonalSpaceInvariantError(SpaceError):
    pass


class SpaceScTeamRepairNotApplicableError(SpaceError):
    """Raised when repair is requested for a non-team Space."""


class SpaceScTeamBindingNotFoundError(SpaceError):
    """Raised when SC has no Team for the Space external reference."""


class SpaceScTeamRepairConflictError(SpaceError):
    """Raised when a conditional backfill loses without a valid binding."""
