from dataclasses import dataclass


@dataclass
class HttpConnectionInfo:
    """HTTP connection information for direct device access.

    This dataclass contains all information needed for a caller to make
    direct HTTP requests to a device's HTTP service.

    Attributes:
        http_url: Full HTTP URL to the container's HTTP service including path
            (e.g., "http://antclaw-a1b2c3.inc.example.net:9999/")
        token: openclawToken for authentication
        target: Target identifier for the connection
            (e.g., "antclaw-a1b2c3.inc.example.net:9999:12345").
            Empty string for platforms that don't populate it.
    """

    http_url: str
    token: str
    target: str = ""
