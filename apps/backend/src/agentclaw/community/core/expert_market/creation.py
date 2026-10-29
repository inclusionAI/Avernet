"""Bind one valid catalogue revision to the existing Manifest creation input."""

from copy import deepcopy
from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit

from agentclaw.community.core.bot_management.expert_origin import ExpertOrigin
from agentclaw.community.core.expert_market.protocols import (
    ExpertCatalogServiceProtocol,
    ExpertCreationServiceProtocol,
)
from agentclaw.community.core.expert_market.models import CompiledExpert, ExpertCreationError
from agentclaw.community.core.expert_market.scanner import canonical_digest


@dataclass(frozen=True)
class ExpertCreationConfig:
    source_url: str
    source_auth: str = ""
    source_repo_id: str = "aiworkbench"


class ExpertCreationService(ExpertCreationServiceProtocol):
    def __init__(
        self, catalog: ExpertCatalogServiceProtocol, config: ExpertCreationConfig
    ):
        self.catalog = catalog
        self.config = config

    def compile(self, *, tenant: str, expert_id: str, engine: str) -> CompiledExpert:
        value = self.catalog.get(tenant=tenant, expert_id=expert_id)
        if engine == "teclaw":
            raise ExpertCreationError(
                "this expert identity combination has no proven Teclaw runtime consumer"
            )
        commit = value["source_commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ExpertCreationError("expert source requires a complete commit SHA")
        url = urlsplit(self.config.source_url)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ExpertCreationError(
                "expert creation source must be configured as a credential-free HTTPS Git URL"
            )
        document = deepcopy(value["manifest_template"])
        if "apply_policy" in document:
            raise ExpertCreationError("apply_policy is not supported for expert templates")
        if engine == "claude_code":
            entries = document["manifest"].get("identity")
            if entries:
                sections = []
                for entry, identity in zip(entries, value["identity"], strict=True):
                    title = entry.get("subpath", entry["type"]).rsplit("/", 1)[-1]
                    sections.append("# " + title + "\n\n" + identity["content"])
                document["manifest"]["identity"] = [
                    {"type": "CLAUDE.md", "content": "\n\n".join(sections)}
                ]
        source = {
            "protocol": "git",
            "url": self.config.source_url,
            "ref": commit,
            "mode": "strict",
        }
        if self.config.source_auth:
            source["auth"] = self.config.source_auth
        document["sources"] = {"aiworkbench": source}
        return CompiledExpert(
            json.dumps(document, ensure_ascii=False),
            ExpertOrigin(
                source_repo_id=self.config.source_repo_id,
                expert_id=expert_id,
                expert_name=value["name"],
                source_commit=commit,
                content_digest=value["content_digest"],
                manifest_digest=canonical_digest(document),
            ),
        )
