"""Stable domain errors exposed by the Skills service contract."""


class InvalidPoolMappingRequestError(ValueError):
    """A Pool mapping request is invalid and safe to reject without mutation."""


__all__ = ["InvalidPoolMappingRequestError"]
