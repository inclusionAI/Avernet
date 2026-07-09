"""Unified command result model for cross-platform use.

Simplified from Arca's CommandResult for PaaS adapter layer.
This dataclass provides a standardized interface for command execution
results across different PaaS platforms (Arca, Sigma, etc.).
"""

from dataclasses import dataclass


@dataclass
class CommandResult:
    """Unified command execution result for cross-platform use.

    Simplified from Arca's CommandResult for PaaS adapter layer.
    Contains only the essential fields that all platforms will support.

    Attributes:
        exit_code: Process exit code (0 for success, non-zero for failure)
        stdout: Standard output as string
        stderr: Standard error as string
        execution_time_ms: Execution duration in milliseconds
        command: The executed command string
        env: Command execution context (input env echo back for tracing)
    """

    exit_code: int
    stdout: str
    stderr: str
    execution_time_ms: int
    command: str
    env: dict[str, str] | None = None
