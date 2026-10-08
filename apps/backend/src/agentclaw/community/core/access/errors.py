"""Access module business exceptions."""


class UserNotFoundError(Exception):
    """Raised when a user record cannot be found."""

    def __init__(self, user_id: str, user_type: str) -> None:
        self.user_id = user_id
        self.user_type = user_type
        super().__init__(f"User not found: user_id={user_id}, user_type={user_type}")
