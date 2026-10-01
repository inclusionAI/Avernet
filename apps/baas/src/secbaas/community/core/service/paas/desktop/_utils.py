"""Local platform utility functions for instance identification and environment access.

Provides helper functions for retrieving the server's instance identifier
(connected_server_instance) and other local platform environment details.
"""

import os

from secbaas.community.core.utils.env_utils import get_local_ip


def get_instance_id() -> str:
    """Get this server's instance identifier for local platform.

    Per D-W03/D-L01: connected_server_instance is auto-filled by secbaas.
    The instance_id is determined from RequestedIP environment variable
    or socket API for local IP address. No yaml config lookup.

    Priority:
    1. RequestedIP environment variable (Ant internal deployment)
    2. Fallback to local IP via socket API (get_local_ip)

    Returns:
        Instance identifier string for connected_server_instance field.

    Examples:
        >>> import os
        >>> os.environ["RequestedIP"] = "10.0.0.1"
        >>> get_instance_id()
        '10.0.0.1'
        >>> del os.environ["RequestedIP"]
        >>> get_instance_id()  # Returns local IP
        '192.168.1.100'
    """
    # Try RequestedIP environment variable first (Ant internal deployment)
    requested_ip = os.environ.get("RequestedIP")
    if requested_ip:
        return requested_ip

    # Fall back to local IP via socket API
    return get_local_ip()
