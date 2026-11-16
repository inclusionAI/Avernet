"""Offline TC migration preserves identity and separates source/config/archive."""

import csv
import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from search_core.providers.sql_catalog import sqlite_catalog_repository


def migrate(*args, **kwargs):
    try:
        module = importlib.import_module("app.tc.migration")
    except ModuleNotFoundError:
        pytest.fail("offline TC migration is not implemented")
    return module.migrate_rows(*args, **kwargs)


def row(worker_id="bot:owner:worker", **changes):
    value = {
        "bot_uuid": worker_id,
        "env": "pre",
        "name": "CSV fallback name",
        "actor_kind": "bot",
        "visibility": "protected",
        "status": "online",
        "is_deleted": "0",
        "bot_info": json.dumps(
            {
                "name": "Source name",
                "summary": "Source description",
                "skills": [{"name": "Actual skill", "description": "Skill detail"}],
                "domains": ["finance"],
                "agent_token": "<test-token>",
            }
        ),
    }
    value.update(changes)
    return value


@pytest.fixture
def repo(tmp_path):
    return sqlite_catalog_repository(str(tmp_path / "cards.sqlite"), environment="pre")


@pytest.mark.parametrize("missing_route", ["card", "status"])
def test_api_import_does_not_count_route_404_as_deleted(missing_route):
    from app.tc.migration import MigrationWriteError
    from app.tc.migration_api import ApiMigrationRepository

    def route(request):
        if request.url.path == "/ready":
            return httpx.Response(200, json={"environment": "pre"})
        canonical = missing_route == "status" and not request.url.path.endswith(
            "index-status"
        )
        return httpx.Response(
            404, json={"detail": "Card not found" if canonical else "Not Found"}
        )

    with httpx.Client(
        base_url="http://tc.test", transport=httpx.MockTransport(route)
    ) as client:
        repository = ApiMigrationRepository(client, "pre")
        with pytest.raises(MigrationWriteError) as failure:
            migrate([row(is_deleted="1")], repository, environment="pre", dry_run=False)
        assert failure.value.report.counts.get("deleted", 0) == 0
        assert failure.value.report.counts["failed"] == 1


def test_csv_maps_real_fields_without_md_expert_or_credentials(repo):
    report = migrate([row()], repo, environment="pre", dry_run=False)
    card = repo.read_card("bot:owner:worker").card
    assert card.agent_id == "bot:owner:worker"
    assert card.labels == {"visibility": ["protected"]}
    assert card.source_card == {
        "name": "Source name",
        "description": "Source description",
        "skills": [{"name": "Actual skill", "description": "Skill detail"}],
        "domains": ["finance"],
        "environment": "pre",
        "type": "bot",
        "availability": "protected",
        "runtime_state": "online",
        "trust_level": "guarded",
        "lifecycle_state": "active",
        "profile_id": "default",
    }
    assert card.resource_type == "agent"
    assert report.counts["migrated"] == 1
    assert "<test-token>" not in json.dumps(report.as_dict())


def test_csv_preserves_user_visibility_independently_of_runtime_state(repo):
    migrate(
        [row(visibility="private", user_visibility="protected", status="offline")],
        repo,
        environment="pre",
        dry_run=False,
    )
    card = repo.read_card("bot:owner:worker").card
    assert card.labels == {"visibility": ["private"], "user_visibility": ["protected"]}
    assert "user_visibility" not in card.source_card


@pytest.mark.parametrize("encoding_depth", [0, 1, 2, 3])
def test_bot_info_accepts_object_and_at_most_two_json_decodes(repo, encoding_depth):
    info = {"name": "Name", "summary": None, "skills": [], "domains": []}
    for _ in range(encoding_depth):
        info = json.dumps(info)
    report = migrate([row(bot_info=info)], repo, environment="pre", dry_run=False)
    if encoding_depth <= 2:
        assert report.counts["migrated"] == 1
        assert (
            repo.read_card("bot:owner:worker").card.source_card["description"] is None
        )
    else:
        assert report.counts["invalid"] == 1
        assert report.reasons == {"invalid_bot_info": 1}
        assert repo.read_card("bot:owner:worker") is None


def test_repeat_run_keeps_revision_and_existing_runtime_config_and_profile_id(repo):
    initial = repo.upsert(
        "bot:owner:worker",
        {
            "runtime_state": "offline",
            "profile_id": "legacy:current",
            "trust_level": "trusted",
            "lifecycle_state": "inactive",
        },
        resource_type="agent",
        search_config={},
    )
    assert repo.save_config(
        initial.card.agent_id, initial.revision, {"fusion_enable": True}
    )
    for _ in range(2):
        report = migrate([row()], repo, environment="pre", dry_run=False)
        assert report.counts["migrated"] == 1
        record = repo.read_card("bot:owner:worker")
        assert record.card.source_card["runtime_state"] == "offline"
        assert record.card.source_card["profile_id"] == "legacy:current"
        assert record.card.source_card["trust_level"] == "trusted"
        assert record.card.source_card["lifecycle_state"] == "inactive"
        assert record.config == {"fusion_enable": True}
        if _ == 0:
            revision = record.revision
        else:
            assert record.revision == revision


def test_legacy_config_uses_config_column_and_old_material_is_only_archived(repo):
    archive = []
    legacy = {
        "profile_id": "used:profile",
        "runtime_state": "offline",
        "config": {"fusion_enable": True},
        "archive": {"profiles": [{"name": "Old profile"}], "md": "Old MD"},
    }
    lookup = lambda environment, worker_id: legacy
    report = migrate(
        [row()],
        repo,
        environment="pre",
        legacy_lookup=lookup,
        archive=archive.append,
        dry_run=False,
    )
    record = repo.read_card("bot:owner:worker")
    assert record.card.source_card["profile_id"] == "used:profile"
    assert record.card.source_card["runtime_state"] == "offline"
    assert record.config == {"fusion_enable": True}
    assert "config" not in record.card.source_card
    assert record.profile is None
    assert archive[0]["legacy_archive"] == legacy["archive"]
    assert archive[0]["outcome"] == "migrated"
    assert len(archive[0]["source_sha256"]) == 64
    assert "Old MD" not in json.dumps(record.card.source_card)
    assert report.counts["migrated"] == 1
    revision = record.revision
    migrate([row()], repo, environment="pre", legacy_lookup=lookup, dry_run=False)
    assert repo.read_card("bot:owner:worker").revision == revision


def test_all_rows_reconcile_and_deleted_cards_do_not_become_active(repo):
    repo.upsert("deleted", {"name": "Old"}, "agent", {})
    repo.upsert("tombstone", {"name": "Old"}, "agent", {})
    repo.remove("tombstone")
    rows = [
        row(),
        row("deleted", is_deleted="1"),
        row("tombstone"),
        row("other-env", env="prod"),
        row("bad", bot_info="invalid-private-payload"),
    ]
    report = migrate(iter(rows), repo, environment="pre", dry_run=False)
    assert report.counts == {
        "total": 5,
        "migrated": 1,
        "deleted": 1,
        "skipped_environment": 1,
        "skipped_tombstone": 1,
        "invalid": 1,
    }
    assert repo.read_card("deleted").card.deleted
    assert repo.read_card("tombstone").card.deleted
    assert repo.read_card("other-env") is None
    assert "private-payload" not in json.dumps(report.as_dict())


def test_dry_run_never_writes(repo):
    report = migrate([row()], repo, environment="pre")
    assert report.dry_run is True
    assert report.counts["migrated"] == 1
    assert repo.read_card("bot:owner:worker") is None


def test_deleted_source_keeps_historical_material_and_deletion_evidence(repo):
    archive = []
    report = migrate(
        [row("deleted", is_deleted="1", bot_info="unusable old JSON")],
        repo,
        environment="pre",
        archive=archive.append,
        dry_run=False,
        legacy_lookup=lambda env, worker: {"archive": {"md": "Old MD"}},
    )
    assert report.counts["deleted"] == 1
    assert archive[0]["legacy_archive"] == {"md": "Old MD"}
    assert archive[0]["outcome"] == "deleted"
    assert repo.read_card("deleted") is None


def test_invalid_skills_or_ambiguous_profile_selection_are_reported(repo):
    invalid_skill = row(bot_info={"name": "Name", "skills": ["invented capability"]})
    ambiguous = {"profile_id": ["first", "second"]}
    report = migrate(
        [invalid_skill, row("ambiguous")],
        repo,
        environment="pre",
        legacy_lookup=lambda env, worker: ambiguous if worker == "ambiguous" else None,
        dry_run=False,
    )
    assert report.counts["invalid"] == 2
    assert report.reasons == {"invalid_skills": 1, "invalid_profile_id": 1}


def test_persistence_failure_raises_sanitized_error_with_partial_report(repo):
    class FailingConfig:
        def __getattr__(self, name):
            return getattr(repo, name)

        def save_config(self, *args):
            raise RuntimeError("secret backend diagnostic")

    with pytest.raises(RuntimeError, match="row 1") as error:
        migrate(
            [row()],
            FailingConfig(),
            environment="pre",
            dry_run=False,
            legacy_lookup=lambda env, worker: {"config": {"fusion_enable": True}},
        )
    assert "secret backend diagnostic" not in str(error.value)
    assert error.value.report.counts["failed"] == 1
    assert error.value.report.counts.get("migrated", 0) == 0


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "legacy",
    [
        {"runtime_state": "busy"},
        {"trust_level": "elevated"},
        {"lifecycle_state": "suspended"},
        {"lifecycle_state": "inactive", "runtime_state": "online"},
    ],
)
def test_invalid_card_state_is_rejected_before_any_write(repo, existing, legacy):
    if existing:
        migrate([row()], repo, environment="pre", dry_run=False)
    before = repo.read_card("bot:owner:worker")
    report = migrate(
        [row(bot_info={"name": "Changed name", "skills": []})],
        repo,
        environment="pre",
        legacy_lookup=lambda env, worker: legacy,
        dry_run=False,
    )
    assert report.counts["invalid"] == 1
    assert report.reasons == {"invalid_card": 1}
    assert repo.read_card("bot:owner:worker") == before


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "invalid_value", ["not-a-bool", [], {"secret": "do-not-print"}]
)
def test_invalid_config_is_rejected_before_card_upsert(repo, existing, invalid_value):
    if existing:
        migrate([row()], repo, environment="pre", dry_run=False)
    before = repo.read_card("bot:owner:worker")
    report = migrate(
        [row(bot_info={"name": "Changed name", "skills": []})],
        repo,
        environment="pre",
        dry_run=False,
        legacy_lookup=lambda env, worker: {"config": {"fusion_enable": invalid_value}},
    )
    assert report.counts["invalid"] == 1
    assert report.reasons == {"invalid_config": 1}
    assert repo.read_card("bot:owner:worker") == before
    assert "do-not-print" not in json.dumps(report.as_dict())


@pytest.mark.parametrize("invalid_part", ["card", "config"])
def test_invalid_inherited_fields_are_checked_without_legacy_overrides(
    repo, invalid_part
):
    migrate([row()], repo, environment="pre", dry_run=False)
    if invalid_part == "card":
        repo.upsert("bot:owner:worker", {"responsibilities": "not-a-list"}, "agent", {})
    else:
        current = repo.read_card("bot:owner:worker")
        assert repo.save_config(
            current.card.agent_id, current.revision, {"fusion_enable": "not-a-bool"}
        )
    before = repo.read_card("bot:owner:worker")
    report = migrate([row()], repo, environment="pre", dry_run=False)
    assert report.counts["invalid"] == 1
    assert report.reasons == {f"invalid_{invalid_part}": 1}
    assert repo.read_card("bot:owner:worker") == before


def test_config_is_normalized_before_persistence_and_repeated_migration_is_stable(repo):
    lookup = lambda env, worker: {
        "config": {"fusion_enable": "false", "custom": {"mode": "kept"}}
    }
    for iteration in range(2):
        report = migrate(
            [row()], repo, environment="pre", legacy_lookup=lookup, dry_run=False
        )
        assert report.counts["migrated"] == 1
        current = repo.read_card("bot:owner:worker")
        assert current.config == {"fusion_enable": False, "custom": {"mode": "kept"}}
        if iteration == 0:
            revision = current.revision
        else:
            assert current.revision == revision


def test_missing_runtime_uses_projectable_offline_default_without_creating_config(repo):
    from app.tc.catalog import TcCatalog

    report = migrate(
        [row(status="")],
        repo,
        environment="pre",
        dry_run=False,
        legacy_lookup=lambda env, worker: {"lifecycle_state": "inactive"},
    )
    assert report.counts["migrated"] == 1
    current = repo.read_card("bot:owner:worker")
    worker = TcCatalog(repo).worker(current)
    assert worker.state.runtime_state.value == "offline"
    assert worker.lifecycle_state == "inactive"
    assert current.config is None


def test_cli_defaults_to_dry_run_and_explicit_sqlite_write_is_isolated(tmp_path):
    source = tmp_path / "source.csv"
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    script = Path(__file__).parents[2] / "scripts" / "migrate_tc_cards.py"
    command = [sys.executable, str(script), str(source), "--environment", "pre"]
    dry = subprocess.run(command, capture_output=True, text=True, check=False)
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["dry_run"] is True
    database = tmp_path / "isolated.sqlite"
    written = subprocess.run(
        command + ["--sqlite", str(database)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)["dry_run"] is False
    with sqlite3.connect(database) as connection:
        tables = {
            value[0]
            for value in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "bcsfuse_agent_cards" in tables
    assert "bcsfuse_tc_agent_cards" not in tables
    assert "search_index_documents" in tables
    assert "tc_search_index_documents" not in tables
    assert sqlite_catalog_repository(
        str(database),
        environment="pre",
        table="bcsfuse_agent_cards",
        document_table="search_index_documents",
    ).read_card("bot:owner:worker")
    assert (
        sqlite_catalog_repository(
            str(database),
            environment="prod",
            table="bcsfuse_agent_cards",
            document_table="search_index_documents",
        ).read_card("bot:owner:worker")
        is None
    )
    assert "<test-token>" not in dry.stdout + written.stdout


def test_cli_legacy_jsonl_joins_full_identity_and_archives_material(tmp_path):
    source = tmp_path / "source.csv"
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    old = tmp_path / "legacy.jsonl"
    old.write_text(
        json.dumps(
            {
                "environment": "pre",
                "worker_id": "bot:owner:worker",
                "profile_id": "legacy:profile",
                "runtime_state": "offline",
                "config": {"fusion_enable": True},
                "archive": {"md": "Archive only"},
            }
        )
        + "\n"
    )
    archive = tmp_path / "archive.jsonl"
    database = tmp_path / "cards.sqlite"
    script = Path(__file__).parents[2] / "scripts" / "migrate_tc_cards.py"
    command = [
        sys.executable,
        str(script),
        str(source),
        "--environment",
        "pre",
        "--sqlite",
        str(database),
        "--legacy-jsonl",
        str(old),
        "--archive",
        str(archive),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    record = sqlite_catalog_repository(
        str(database),
        environment="pre",
        table="bcsfuse_agent_cards",
        document_table="search_index_documents",
    ).read_card("bot:owner:worker")
    assert record.card.source_card["runtime_state"] == "offline"
    assert record.card.source_card["profile_id"] == "legacy:profile"
    assert record.config == {"fusion_enable": True}
    assert json.loads(archive.read_text())["legacy_archive"] == {"md": "Archive only"}
    assert archive.stat().st_mode & 0o777 == 0o600
    assert "Archive only" not in result.stdout
    old.write_text(old.read_text() * 2)
    duplicate = subprocess.run(
        command[:-2], capture_output=True, text=True, check=False
    )
    assert duplicate.returncode == 1
    assert (
        json.loads(duplicate.stdout)["error"]
        == "invalid_or_unavailable_input_or_local_catalog"
    )


def test_archive_failure_is_not_reported_as_completed_migration(repo):
    def broken_archive(record):
        raise OSError("sensitive path")

    with pytest.raises(RuntimeError) as error:
        migrate([row()], repo, environment="pre", archive=broken_archive, dry_run=False)
    assert error.value.report.reasons == {"archive_failed": 1}
    assert error.value.report.counts.get("migrated", 0) == 0
    assert "sensitive path" not in str(error.value)
