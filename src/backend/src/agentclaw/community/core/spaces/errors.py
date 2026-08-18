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
