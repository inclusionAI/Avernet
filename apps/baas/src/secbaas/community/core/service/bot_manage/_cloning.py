"""Bot clone policy. No service decision holds a repository session open."""

from typing import Any

from secbaas.community.core.repository.bot import BotRecordConflictError, BotRepository


def clone_bot_record(
    repository: BotRepository,
    *,
    source_bot_id: int,
    tenant: str,
    env: str,
    status: str,
    extra_config: dict[str, Any] | None = None,
    name: str | None = None,
    template_uuid: str | None = None,
    modifier: str = "system",
) -> int:
    """Read a snapshot, construct the clone without a session, then INSERT.

    The unique key arbitrates concurrent inserts, including the race after the
    optional PENDING pre-check. Historical soft-deleted rows stay untouched.
    This intentionally does not promise a transactionally frozen source.
    """
    source = repository.get_by_id(source_bot_id, tenant, env)
    if source is None:
        raise ValueError(f"Source bot not found: {source_bot_id}")
    if status == "PENDING":
        incumbent = repository.get_by_bot_uuid(source.bot_uuid, tenant, env, status)
        if incumbent is not None:
            raise BotRecordConflictError(
                f"PENDING bot record already exists for bot_uuid={source.bot_uuid} "
                f"(id={incumbent.id})"
            )
    return repository.insert_bot(
        bot_uuid=source.bot_uuid,
        tenant=source.tenant,
        env=source.env,
        domain=source.domain,
        creator=source.creator,
        modifier=modifier,
        status=status,
        name=name if name is not None else source.name,
        description=source.description,
        template_uuid=template_uuid
        if template_uuid is not None
        else source.template_uuid,
        replica_desired=source.replica_desired,
        replica_minimum=source.replica_minimum,
        replica_maximum=source.replica_maximum,
        auto_scaling_enabled=source.auto_scaling_enabled,
        sla_grade=source.sla_grade,
        extra_config=extra_config if extra_config is not None else source.extra_config,
    )


def try_insert_pending_bot(
    repository: BotRepository,
    *,
    source_bot_id: int,
    tenant: str,
    env: str,
    extra_config: dict[str, Any] | None = None,
    name: str | None = None,
    template_uuid: str | None = None,
    modifier: str = "system",
) -> int:
    """Reserve a fresh PENDING slot, never deleting or reviving another clone."""
    return clone_bot_record(
        repository,
        source_bot_id=source_bot_id,
        tenant=tenant,
        env=env,
        status="PENDING",
        extra_config=extra_config,
        name=name,
        template_uuid=template_uuid,
        modifier=modifier,
    )
