"""TemplateRepository Protocol.

Defines the abstract interface for template persistence operations.
The unified ORM implementation is provided in plugins/template_repository.py.
"""
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class TemplateRepository(Protocol):
    """Protocol for template repository implementations.

    Implementation: a single unified ORM body
    (plugins.template_repository.TemplateRepository) runs on both prod
    OceanBase and local SQLite via the injected DatabasePlugin.
    """

    def insert(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new template record.

        Args:
            template_data: Dictionary with template fields (bot_id, ext, etc.)

        Returns:
            Created template record as dictionary
        """
        ...

    def get_by_bot_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            Template record as dictionary, or None if not found
        """
        ...

    def update_by_bot_id(self, bot_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update template by bot_id.

        Args:
            bot_id: Bot ID
            update_data: Dictionary with fields to update

        Returns:
            Updated template record as dictionary, or None if not found
        """
        ...

    def delete_by_bot_id(self, bot_id: str) -> bool:
        """Delete template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if deleted, False if not found
        """
        ...

    def exists_by_bot_id(self, bot_id: str) -> bool:
        """Check if a template exists for the given bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if template exists, False otherwise
        """
        ...

    def list_by_architect_bot_id(self, architect_bot_id: str) -> List[Dict[str, Any]]:
        """List templates whose ext JSON contains the given architect_bot_id.

        Used to find all application coding bots associated with a
        domain architect bot.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of template records
        """
        ...
