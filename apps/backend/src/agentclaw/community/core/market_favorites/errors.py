"""Domain errors for market favorites."""


class FavoriteTargetInvalidError(ValueError):
    pass


class FavoriteNotFoundError(LookupError):
    pass
