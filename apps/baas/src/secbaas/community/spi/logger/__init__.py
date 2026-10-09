"""Logger SPI — pluggable logger abstraction.

Provides the ``LoggerPlugin`` protocol that allows the logging backend
to be selected at module init time.
"""

from ._protocols import LoggerPlugin

__all__ = [
    "LoggerPlugin",
]
