"""Base classes for LLM-powered diagnostic checks."""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.harness.models import Finding
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin

if TYPE_CHECKING:
    from agentclaw.community.di.config import KbConfig

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticContext:
    """Runtime context injected by ContentScanner into each Diagnostic.

    Every read_file call goes through to BotProfile to ensure the latest
    file content is always returned (no caching).
    """

    llm: LLM
    bot_profile: BotProfile
    entity_type: str
    entity_id: str
    bot_id: str
    operator_id: str = ""
    bot_meta: dict[str, Any] = field(default_factory=dict)
    bot_publish_id: str | None = None
    # Optional: supplied by ContentScanner (DI-injected). ``None`` when a
    # caller builds a context without it — diagnostics that need it must
    # null-guard (see ToolsMcpFormatDiagnostic).
    mcp_center: MCPCenterPlugin | None = None
    # BCSFuse host, supplied by ContentScanner from BcsFuseConfig (corp env
    # overlays set the ``bcsfuse`` yaml block). Empty ⇒ the D-SOUL profile
    # fetch is skipped (feature-off).
    bcsfuse_base_url: str = ""
    # Internal KB config, supplied by ContentScanner from KbConfig (corp env
    # overlays set the ``kb`` yaml block). ``None`` / empty base_url ⇒ the
    # D-TOOLS-002 KB query is skipped (feature-off).
    kb_config: "KbConfig | None" = None

    async def read_file(self, file_type: str) -> str:
        """Read bot file content from BotProfile. Returns empty string on failure.

        Always fetches fresh content — no caching — so that patches applied
        between scans are reflected immediately.

        If bot_publish_id is set, reads files from the specific publish version.
        """
        try:
            ref = await self.bot_profile.read_file(
                self.entity_type,
                self.entity_id,
                self.bot_id,
                file_type,
                self.operator_id,
                publish_id=self.bot_publish_id,
            )
            if ref.content.strip():
                logger.info(
                    "[DiagnosticContext] read_file OK bot=%s file_type=%s len=%d",
                    self.bot_id, file_type, len(ref.content),
                )
            else:
                logger.warning(
                    "[DiagnosticContext] read_file EMPTY bot=%s entity_type=%s entity_id=%s file_type=%s",
                    self.bot_id, self.entity_type, self.entity_id, file_type,
                )
            return ref.content
        except Exception as e:
            logger.warning(
                "[DiagnosticContext] read_file FAILED bot=%s file_type=%s error=%s",
                self.bot_id, file_type, e,
            )
            return ""


class Diagnostic(ABC):
    """Base class for a single diagnostic check item.

    Each diagnostic is an independent, extensible unit that:
    - Has its own system prompt for LLM analysis
    - Can gather whatever data it needs via DiagnosticContext
    - Returns at most one Finding after LLM analysis (rule_id, rule_name,
      severity, file_type are all fixed by the class; LLM only returns message)
    """

    id: str = ""
    name: str = ""
    severity: str = ""  # "critical" | "warning" | "info"
    file_type: str = ""  # target file, e.g. "AGENTS.md", "SAFETY.md"
    suggested_template_ids: list[int] = [1111]
    fix_suggestion: str = ""  # recommended fix template included in system_prompt
    system_prompt: str = ""

    @abstractmethod
    async def analyze(self, ctx: DiagnosticContext) -> list[Finding]:
        """Execute this diagnostic and return findings."""

    def _analyze_response(
        self,
        raw_text: str,
        bot_id: str,
    ) -> list[Finding]:
        """Parse LLM response into 0 or 1 Finding using fixed diagnostic fields.

        Convenience helper for subclasses — handles LLM-disabled, no-issue
        markers, and message extraction so each subclass doesn't need to
        duplicate this logic.
        """
        from agentclaw.community.core.harness.services.llm_response_parser import (
            parse_diagnostic_response,
        )
        return parse_diagnostic_response(
            rule_id=self.id,
            rule_name=self.name,
            severity=self.severity,
            file_type=self.file_type,
            suggested_template_ids=self.suggested_template_ids,
            bot_id=bot_id,
            raw_text=raw_text,
        )
