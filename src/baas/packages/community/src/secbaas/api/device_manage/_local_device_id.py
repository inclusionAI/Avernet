"""Local device ID parser.

Defines the LocalDeviceId class for parsing and formatting device IDs
in the container_id--machine_id--user_id format.
"""

from dataclasses import dataclass


class InvalidLocalDeviceIdError(ValueError):
    """Raised when raw_paas_device_id format is invalid."""

    pass


@dataclass(frozen=True)
class LocalDeviceId:
    """Local platform device ID parser and formatter.

    Device IDs use three-part format: container_id--machine_id--user_id

    Examples:
        - mycontainer--mac-studio--zhangsan
        - sandbox-abc123--desktop-win11--lisi
    """

    SEPARATOR = "--"

    container_id: str
    machine_id: str
    user_id: str

    @classmethod
    def parse(cls, raw_paas_device_id: str) -> "LocalDeviceId":
        """Parse raw_paas_device_id in format: container_id--machine_id--user_id

        Args:
            raw_paas_device_id: The raw device ID string to parse

        Returns:
            LocalDeviceId instance with parsed components

        Raises:
            InvalidLocalDeviceIdError: If format is invalid (not exactly 3 parts)
        """
        parts = raw_paas_device_id.split(cls.SEPARATOR)
        if len(parts) != 3:
            raise InvalidLocalDeviceIdError(
                f"Expected format: container_id--machine_id--user_id, "
                f"got: {raw_paas_device_id}"
            )
        return cls(
            container_id=parts[0],
            machine_id=parts[1],
            user_id=parts[2],
        )

    def format(self) -> str:
        """Returns raw_paas_device_id without @template_id suffix.

        Returns:
            Formatted device ID string: container_id--machine_id--user_id
        """
        return f"{self.container_id}{self.SEPARATOR}{self.machine_id}{self.SEPARATOR}{self.user_id}"
