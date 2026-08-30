"""Complete, fail-closed lineage scan over replayable Service artifacts."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol import (
    ServiceArtifactLineage,
    ServiceArtifactLineageReaderProtocol,
    ServiceArtifactReference,
    UnknownServiceArtifact,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.services.service_artifact_refs import (
    exact_center_refs_from_artifact_ext,
)


_PAGE_SIZE = 100
_AUDIT_ONLY_STATUSES = {PublishStatus.DRAFT.value, PublishStatus.FAILED.value}


class ServiceArtifactLineageReader(ServiceArtifactLineageReaderProtocol):
    """Hide pagination, offload resolution and both Artifact wire shapes."""

    @inject
    def __init__(self, repository: BotPublishRepositoryProtocol) -> None:
        self._repository = repository

    def scan(self, *, skill_uuid: str, env: str) -> ServiceArtifactLineage:
        references: list[ServiceArtifactReference] = []
        unknown: list[UnknownServiceArtifact] = []
        cursor: int | None = None
        seen_cursors: set[int] = set()

        while True:
            try:
                page = self._repository.list_lineage_candidates_page(
                    env=env,
                    after_id=cursor,
                    limit=_PAGE_SIZE,
                )
            except Exception:
                unknown.append(
                    UnknownServiceArtifact(
                        resource_id="artifact-scan",
                        display_name="Service Artifact lineage is unreadable",
                    )
                )
                break

            ids = [record.id for record in page.records]
            if (
                any(value is None for value in ids)
                or ids != sorted(ids)
                or len(ids) != len(set(ids))
                or (cursor is not None and ids and int(ids[0]) <= cursor)
            ):
                unknown.append(self._pagination_unknown())
                break

            for record in page.records:
                if record.status in _AUDIT_ONLY_STATUSES:
                    continue
                try:
                    status = PublishStatus(record.status)
                    ext = record.ext
                    if not isinstance(ext, dict):
                        raise ValueError("artifact ext is missing")
                    has_artifact = any(
                        key in ext
                        for key in (
                            "migration_path",
                            "build_target_path",
                            "skills_manifest",
                            "config_artifact",
                        )
                    )
                    if not has_artifact:
                        # BUILDING has not crossed the replayable commit seam yet.
                        if status is PublishStatus.BUILDING:
                            continue
                        raise ValueError("replayable record has no artifact")
                    refs = exact_center_refs_from_artifact_ext(ext)
                except Exception:
                    unknown.append(
                        UnknownServiceArtifact(
                            resource_id=str(record.id),
                            display_name=f"{record.name} artifact is unreadable",
                        )
                    )
                    continue

                for ref in refs:
                    if ref.skill_uuid != skill_uuid:
                        continue
                    references.append(
                        ServiceArtifactReference(
                            publish_id=int(record.id),
                            source_bot_id=record.source_bot_id,
                            source_bot_name=record.name,
                            service_version=record.version,
                            sc_version_number=ref.sc_version_number,
                        )
                    )

            if page.complete:
                if page.next_cursor is not None:
                    unknown.append(self._pagination_unknown())
                break
            next_cursor = page.next_cursor
            if (
                next_cursor is None
                or not page.records
                or next_cursor != page.records[-1].id
                or next_cursor in seen_cursors
                or (cursor is not None and next_cursor <= cursor)
            ):
                unknown.append(self._pagination_unknown())
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        return ServiceArtifactLineage(
            references=tuple(references),
            unknown=tuple(unknown),
        )

    @staticmethod
    def _pagination_unknown() -> UnknownServiceArtifact:
        return UnknownServiceArtifact(
            resource_id="artifact-scan",
            display_name="Service Artifact lineage scan is incomplete",
        )


__all__ = ["ServiceArtifactLineageReader"]
