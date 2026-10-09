"""Domain errors for Bot collaborator and editor management."""


class CollaboratorServiceError(Exception):
    """Base error for collaborator management."""


class PermissionDeniedError(CollaboratorServiceError):
    """The actor lacks the required Bot permission."""


class CollaboratorNotFoundError(CollaboratorServiceError):
    """The addressed collaborator relation does not exist."""


class CollaboratorAlreadyExistsError(CollaboratorServiceError):
    """The user is already a collaborator on the Bot."""


class CannotRemoveSelfError(CollaboratorServiceError):
    """A non-owner attempted to remove their own relation through admin CRUD."""


class BotNotFoundError(CollaboratorServiceError):
    """The addressed Bot does not exist in the selected owner scope."""


class BotNotServiceTypeError(CollaboratorServiceError):
    """The Bot does not support collaborator management."""


class InvalidCollaboratorRoleError(CollaboratorServiceError):
    """The requested collaborator role is unsupported."""


class CollaboratorSpaceMembershipError(CollaboratorServiceError):
    """The editor candidate is not a live member of the Bot's Team Space."""


__all__ = [
    "BotNotFoundError",
    "BotNotServiceTypeError",
    "CannotRemoveSelfError",
    "CollaboratorAlreadyExistsError",
    "CollaboratorNotFoundError",
    "CollaboratorServiceError",
    "CollaboratorSpaceMembershipError",
    "InvalidCollaboratorRoleError",
    "PermissionDeniedError",
]
