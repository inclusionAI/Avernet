"""The service: what it stores, what it refuses, and what it never touches.

Backed by a fake repository so these assert the *service's* rules — the storage
round trip has its own test over a real database in
``tests/community/repository/bot/test_bot_config_manifest_repository.py``.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.core.bot_config_manifest.capabilities import ManifestSection
from agentclaw.community.core.bot_config_manifest.repository.models import (
    BotConfigManifestRecord,
)
from agentclaw.community.core.bot_config_manifest.services.config_manifest_service import (
    MAX_MODIFIER_CHARS,
    BotConfigManifestService,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
)

_DOC = "schema_version: 1\nmanifest:\n  skills: []\n"


class _FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], BotConfigManifestRecord] = {}
        self.writes: list[dict] = []

    def get(self, *, env, entity_id, bot_id):
        return self.rows.get((env, entity_id, bot_id))

    def upsert(self, *, env, entity_id, bot_id, document, size_bytes,
               schema_version, modifier):
        self.writes.append(
            {
                "env": env,
                "entity_id": entity_id,
                "bot_id": bot_id,
                "document": document,
                "size_bytes": size_bytes,
                "schema_version": schema_version,
                "modifier": modifier,
            }
        )
        record = BotConfigManifestRecord(
            id=1,
            env=env,
            entity_id=entity_id,
            bot_id=bot_id,
            document=document,
            size_bytes=size_bytes,
            schema_version=schema_version,
            modifier=modifier,
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
        )
        self.rows[(env, entity_id, bot_id)] = record
        return record

    def delete(self, *, env, entity_id, bot_id):
        return self.rows.pop((env, entity_id, bot_id), None) is not None


class _TeclawTest:
    @staticmethod
    def is_teclaw(active_engine):
        return (active_engine or "").strip().lower() == "teclaw"


@pytest.fixture
def repository():
    return _FakeRepository()


@pytest.fixture
def service(repository):
    return BotConfigManifestService(repository, lambda: _TeclawTest())


def test_a_bot_that_never_had_a_manifest_reads_as_absent_not_as_an_error(service):
    assert service.get(entity_id="ent", bot_id="bot") is None


def test_put_stores_the_document_and_stamps_the_actor(service, repository):
    result = service.put(
        entity_id="ent",
        bot_id="bot",
        document=_DOC,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    assert result.record.document == _DOC
    assert result.record.schema_version == 1
    assert result.record.modifier == "u1"
    assert repository.writes[0]["size_bytes"] == len(_DOC.encode("utf-8"))


def test_put_stores_the_caller_bytes_not_a_re_serialisation(service, repository):
    """A YAML round trip preserves the document's value, and ``script.body`` is a
    shell body whose bytes are its meaning."""
    document = (
        "schema_version: 1\n"
        "script:\n"
        "  body: |\n"
        "    #!/bin/bash\n"
        "    echo '$(id)' \"EOF\" {token}\n"
    )
    service.put(
        entity_id="ent",
        bot_id="bot",
        document=document,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    assert repository.writes[0]["document"] == document


def test_put_accepts_a_git_source_document(service, repository):
    """The admission flip this fix delivers, pinned end to end: a document
    declaring named and git sources for skills/identity stores through the
    same PUT path every URL-source document always used — the one-file
    review given to W7 found the delivered runtime gated off behind the
    still-closed capability rows."""
    document = (
        "schema_version: 1\n"
        "sources:\n"
        "  repo:\n"
        "    git: https://code.example.com/team/skills.git\n"
        "    ref: v1.2.0\n"
        "    subpath: packages/demo\n"
        "manifest:\n"
        "  identity:\n"
        "    - type: SOUL.md\n"
        "      from: repo\n"
        "  resources:\n"
        "    - path: assets/logo.png\n"
        "      source: https://cdn.example.com/logo.png\n"
        "      digest: sha256:" + "0" * 64 + "\n"
    )
    result = service.put(
        entity_id="ent",
        bot_id="bot",
        document=document,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    assert result.record.schema_version == 1
    assert repository.writes[0]["size_bytes"] == len(document.encode("utf-8"))


def test_a_refused_document_is_not_written_at_all(service, repository):
    """All-or-nothing: one unsupported category refuses the whole document."""
    document = (
        "schema_version: 1\nmanifest:\n  engine_config:\n    config:\n      model: m\n"
    )
    with pytest.raises(ManifestValidationError):
        service.put(
            entity_id="ent",
            bot_id="bot",
            document=document,
            modifier="u1",
            active_engine="openclaw",
            bot_type="personal",
        )
    assert repository.writes == []


def test_a_refusal_leaves_a_previously_accepted_document_in_place(service):
    service.put(
        entity_id="ent",
        bot_id="bot",
        document=_DOC,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    with pytest.raises(ManifestValidationError):
        service.put(
            entity_id="ent",
            bot_id="bot",
            document="schema_version: 9\n",
            modifier="u2",
            active_engine="openclaw",
            bot_type="personal",
        )
    assert service.get(entity_id="ent", bot_id="bot").document == _DOC


def test_an_over_long_actor_is_truncated_rather_than_failing_the_write(
    service, repository
):
    """The actor is the platform's own composition meeting a legitimately long
    user id; failing the caller's write for it would blame them for our
    formatting."""
    modifier = "app:7:on-behalf-of:" + "u" * 2000
    service.put(
        entity_id="ent",
        bot_id="bot",
        document=_DOC,
        modifier=modifier,
        active_engine="openclaw",
        bot_type="personal",
    )
    stored = repository.writes[0]["modifier"]
    assert len(stored) == MAX_MODIFIER_CHARS
    assert stored.startswith("app:7:on-behalf-of:")


def test_warnings_ride_back_with_the_record(service):
    document = (
        "schema_version: 1\n"
        "sources:\n"
        "  assets:\n"
        "    url: https://cdn.example.com/assets/\n"
        "manifest:\n"
        "  skills: []\n"
    )
    result = service.put(
        entity_id="ent",
        bot_id="bot",
        document=document,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    assert result.warnings and "assets" in result.warnings[0]


def test_delete_is_idempotent(service):
    assert service.delete(entity_id="ent", bot_id="bot") is False
    service.put(
        entity_id="ent",
        bot_id="bot",
        document=_DOC,
        modifier="u1",
        active_engine="openclaw",
        bot_type="personal",
    )
    assert service.delete(entity_id="ent", bot_id="bot") is True
    assert service.delete(entity_id="ent", bot_id="bot") is False


def test_validate_needs_no_bot_record(service, repository):
    """W13's entry point: the first leg of bot creation has no ``ac_bots`` row."""
    result = service.validate(
        document=_DOC, active_engine="openclaw", bot_type="personal"
    )
    assert result.schema_version == 1
    assert repository.writes == []


def test_the_read_and_write_paths_share_one_capability_answer(service):
    """The acceptance criterion: ``/capabilities`` cannot promise what ``PUT``
    then refuses."""
    bot = {"active_engine": "teclaw", "bot_type": "personal"}
    from_record = service.capabilities_for_bot(bot)
    from_fields = service.resolve_capabilities(
        active_engine="teclaw", bot_type="personal"
    )
    assert from_record == from_fields

    script_doc = "schema_version: 1\nscript:\n  body: |\n    echo hi\n"
    assert not from_record.supports(ManifestSection.SCRIPT)
    with pytest.raises(ManifestValidationError):
        service.put(
            entity_id="ent",
            bot_id="bot",
            document=script_doc,
            modifier="u1",
            active_engine="teclaw",
            bot_type="personal",
        )


def test_the_uniqueness_key_fits_innodbs_index_limit():
    """A utf8mb4 index key is capped at 3072 bytes; over it MySQL refuses the
    CREATE TABLE outright and the table simply would not exist in production.

    The key is the logical one — ``(avernet_tenant, env, entity_id, bot_id)`` —
    so these widths are a budget rather than free choices. It fits because
    ``entity_id`` is 256 characters here and not the 1024 it has on ``ac_bots``,
    which alone would be 4096 bytes and over the cap.

    SQLite enforces neither the index limit nor ``VARCHAR`` widths, so the
    entire local suite would pass against a table that can never be created —
    hence an arithmetic check rather than a boot test. It is what would catch
    someone widening ``entity_id`` back to match its source.
    """
    from agentclaw.community.core.bot_config_manifest.repository.models import (
        BotConfigManifestModel,
    )

    unique = [
        c
        for c in BotConfigManifestModel.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    ]
    assert unique, "the table must keep a uniqueness constraint"

    for constraint in unique:
        chars = sum(getattr(col.type, "length", 0) or 0 for col in constraint.columns)
        assert chars * 4 <= 3072, (
            f"{constraint.name} is {chars} chars = {chars * 4} utf8mb4 bytes, "
            f"over InnoDB's 3072-byte index-key limit"
        )

    # The key names the logical identity directly. If a surrogate is ever
    # reintroduced, the injectivity argument comes back with it (see the
    # colliding-boundaries test in the repository suite), so the change should
    # be deliberate rather than incidental.
    assert {col.name for col in unique[0].columns} == {
        "avernet_tenant",
        "env",
        "entity_id",
        "bot_id",
    }


def test_the_document_column_can_hold_a_document_the_api_accepts():
    """The size limit and the column have to agree, and on MySQL they only do
    because of the ``MEDIUMTEXT`` variant.

    ``MAX_DOCUMENT_BYTES`` is 65,536 and the validator's check is a strict
    ``>``, so a document of exactly 64 KiB is accepted. MySQL ``TEXT`` — what
    plain ``Text`` renders as — holds 65,535, one byte less. A deployment that
    emits its own DDL (``create_schema`` defaults to True) would then take an
    accepted document and either refuse the write or silently truncate it,
    breaking the byte-exact guarantee this feature is built on.

    Asserted against the compiled MySQL DDL because the repository tests run on
    SQLite, where ``TEXT`` is unbounded and the boundary is invisible.
    """
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    from agentclaw.community.core.bot_config_manifest.repository.models import (
        BotConfigManifestModel,
    )
    from agentclaw.community.core.bot_config_manifest.schema import (
        MAX_DOCUMENT_BYTES,
    )

    #: MySQL's ``TEXT`` capacity, in bytes.
    mysql_text_capacity = 65_535
    assert MAX_DOCUMENT_BYTES > mysql_text_capacity, (
        "the limit no longer exceeds TEXT — if it was lowered on purpose, this "
        "test and the column comment should say so"
    )

    ddl = str(CreateTable(BotConfigManifestModel.__table__).compile(dialect=mysql.dialect()))
    document_line = next(line for line in ddl.splitlines() if "document" in line)
    assert "MEDIUMTEXT" in document_line, document_line
