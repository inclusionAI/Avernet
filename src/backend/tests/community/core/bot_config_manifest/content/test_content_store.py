"""Contract tests for the manifest content store (W11, #1510).

Every §2.8 property gets a pinned test, and the acceptance lines of #1510
live here: the receipt is verified before anything is written, the read
path proves what it returns, the stored URLs carry neither userinfo nor
query strings, and the only credential-shaped thing in this layer is a
name. The repository behind the service is an in-memory fake — its real
persistence contract has its own file next to W1's repository tests.
"""
from __future__ import annotations

import hashlib
import pathlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.content.errors import (
    ContentIntegrityError,
    ContentMissingError,
    ContentStoreError,
)
from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
    StoredContentRecord,
)
from agentclaw.community.core.bot_config_manifest.content.service import (
    ManifestContentService,
)
from agentclaw.community.core.bot_config_manifest.content.settings import (
    DEFAULT_CONTENT_STORE_DIR,
    content_store_root_from_config,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchedObject,
)

BODY = b"manifest-bytes" * 64
BODY_SHA = "sha256:" + hashlib.sha256(BODY).hexdigest()
SCOPE = ContentScope(env="dev", entity_id="ent_a", bot_id="bot_1")


class _FakeRepository:
    """In-memory provenance log: same semantics, no database.

    ``fail_next`` simulates a row-insert failure after the blob landed —
    the store's write order is blob-first, and that path needs pinning.
    """

    def __init__(self) -> None:
        self.rows: list[StoredContentRecord] = []
        self.fail_next: bool = False

    def add(self, record: StoredContentRecord) -> StoredContentRecord:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("row insert failed")
        stored = record.model_copy(update={"id": len(self.rows) + 1})
        self.rows.append(stored)
        return stored

    def records_for(self, *, env, entity_id, bot_id, limit=50):
        matched = [
            r
            for r in self.rows
            if (r.env, r.entity_id, r.bot_id) == (env, entity_id, bot_id)
        ]
        return list(reversed(matched))[:max(0, limit)]


def _fetched(body: bytes = BODY, *, url: str = "https://content.example/a.bin",
            sha: str | None = None, size: int | None = None,
            content_type: str | None = "application/octet-stream") -> FetchedObject:
    return FetchedObject(
        bytes=body,
        sha256=sha or "sha256:" + hashlib.sha256(body).hexdigest(),
        url=url,
        content_type=content_type,
        fetched_at=datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc),
        size_bytes=size if size is not None else len(body),
    )


def _service(tmp_path: Path) -> tuple[ManifestContentService, _FakeRepository]:
    repo = _FakeRepository()
    return ManifestContentService(repo, tmp_path / "store"), repo


def _blob_file(tmp_path: Path, digest: str) -> Path:
    """The blob file's location for a digest under the standard service root."""
    hex_digest = digest.partition(":")[2]
    return (
        tmp_path / "store" / "blobs" / hex_digest[:2] / hex_digest
    )


# --- the receipt contract -----------------------------------------------------


def test_store_then_read_roundtrips_the_platform_copy(tmp_path):
    service, repo = _service(tmp_path)
    stored = service.store(
        _fetched(), scope=SCOPE, source_url="https://content.example/a.bin"
    )
    assert stored.digest == BODY_SHA
    assert service.read(BODY_SHA) == BODY
    # The audit read surfaces the same receipt — scope-shaped, like store().
    assert [r.digest for r in service.records(scope=SCOPE)] == [BODY_SHA]


def test_records_limit_none_defaults_and_an_explicit_value_bounds(tmp_path):
    # None means the repository protocol's DEFAULT_RECORD_LIMIT (the one
    # definition of the bound — no drifting literal here); an explicit
    # value bounds the audit read itself.
    service, _ = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    service.store(_fetched(url="https://mirror.example/a.bin"), scope=SCOPE,
                  source_url="https://mirror.example/a.bin")
    assert len(service.records(scope=SCOPE)) == 2
    assert len(service.records(scope=SCOPE, limit=1)) == 1


def test_a_receipt_that_disagrees_with_its_bytes_is_refused(tmp_path):
    # The layer whose job is "bytes we can prove" refuses the hand-off before
    # anything is written — nothing on disk, nothing in the log.
    service, repo = _service(tmp_path)
    liar = "sha256:" + "00" * 32
    with pytest.raises(ContentIntegrityError, match="receipt"):
        service.store(_fetched(sha=liar), scope=SCOPE,
                      source_url="https://content.example/a.bin")
    assert repo.rows == []
    assert not (tmp_path / "store" / "blobs").exists()


def test_a_declared_size_that_disagrees_is_refused(tmp_path):
    service, repo = _service(tmp_path)
    with pytest.raises(ContentIntegrityError, match="size"):
        service.store(_fetched(size=17), scope=SCOPE,
                      source_url="https://content.example/a.bin")
    assert repo.rows == []


def test_reading_a_missing_address_is_terminal_never_a_refetch(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(ContentMissingError, match="no stored content"):
        service.read(BODY_SHA)


def test_a_corrupted_blob_fails_its_digest_on_read(tmp_path):
    # The store verified these bytes when writing them — a hash mismatch on
    # read means disk corruption, and delivery must not ship it.
    service, _ = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    blob = _blob_file(tmp_path, BODY_SHA)
    blob.write_bytes(blob.read_bytes() + b"tamper")
    with pytest.raises(ContentIntegrityError, match="fails its own digest"):
        service.read(BODY_SHA)


def test_a_malformed_address_is_refused_before_any_io(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(ContentStoreError, match="untrusted content address"):
        service.read("md5:zz")


# --- the blob tree ------------------------------------------------------------


def test_blobs_are_content_addresses_sharded_two_deep(tmp_path):
    service, _ = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    hex_digest = BODY_SHA.partition(":")[2]
    blob = (
        tmp_path
        / "store"
        / "blobs"
        / hex_digest[:2]
        / hex_digest
    )
    assert blob.is_file()
    assert blob.read_bytes() == BODY


def test_the_same_bytes_are_written_once_ever(tmp_path):
    # Content addressing is the dedup: one file, two provenance rows — the
    # second fetch of the same content is an audit event, not a second copy.
    service, repo = _service(tmp_path)
    service.store(_fetched(url="https://content.example/a.bin"), scope=SCOPE,
                  source_url="https://content.example/a.bin")
    service.store(_fetched(url="https://mirror.example/a.bin"), scope=SCOPE,
                  source_url="https://mirror.example/a.bin", modifier="u2")
    files = [
        p for p in (tmp_path / "store" / "blobs").rglob("*")
        if p.is_file() and ".tmp-" not in p.name
    ]
    assert len(files) == 1
    assert len(repo.rows) == 2


def test_no_temp_files_survive_a_store(tmp_path):
    service, _ = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    strays = [p for p in (tmp_path / "store" / "blobs").rglob(".tmp-*")]
    assert strays == []


# --- widths: every refusal happens before the blob lands (终审 H-1/M-1) ------


def _assert_no_side_effects(tmp_path, repo):
    assert repo.rows == []
    assert not (tmp_path / "store" / "blobs").exists()


def test_an_oversized_source_wire_header_is_refused_before_the_blob(tmp_path):
    # Content-Type is source-controlled wire data with no length cap of its
    # own; the column is varchar(256). SQLite (the test DB) does not enforce
    # varchar widths, so without this store-level check an oversized value
    # would land only at the row insert — after the blob is already on disk.
    service, repo = _service(tmp_path)
    with pytest.raises(ContentStoreError, match="content_type"):
        service.store(_fetched(content_type="x" * 257), scope=SCOPE,
                      source_url="https://content.example/a.bin")
    _assert_no_side_effects(tmp_path, repo)


def test_an_oversized_url_is_refused_before_the_blob(tmp_path):
    service, repo = _service(tmp_path)
    long_url = "https://content.example/" + "a" * 3000 + ".bin"
    with pytest.raises(ContentStoreError, match="source_url"):
        service.store(_fetched(), scope=SCOPE, source_url=long_url)
    _assert_no_side_effects(tmp_path, repo)


def test_an_oversized_credential_name_is_refused_before_the_blob(tmp_path):
    service, repo = _service(tmp_path)
    with pytest.raises(ContentStoreError, match="credential_name"):
        service.store(_fetched(), scope=SCOPE,
                      source_url="https://content.example/a.bin",
                      credential_name="n" * 129)
    _assert_no_side_effects(tmp_path, repo)


def test_a_huge_query_does_not_overflow_because_it_is_never_stored(tmp_path):
    # The width check measures the SANITIZED URL: a 4000-char signed query is
    # dropped before the measurement, so what the column has to hold is the
    # path-only shape and it fits. Query removal is doing load-bearing work.
    service, repo = _service(tmp_path)
    url = "https://content.example/ok.bin?sign=" + "s" * 4000
    stored = service.store(_fetched(url=url), scope=SCOPE, source_url=url)
    assert stored.source_url == "https://content.example/ok.bin"


def test_a_path_that_overflows_the_column_is_refused_before_the_blob(tmp_path):
    # M-1: when the sanitized shape itself overflows, the refusal must land
    # with zero side effects — the blob write happens after every refusal.
    service, repo = _service(tmp_path)
    url = "https://content.example/" + "b" * 2600 + ".bin"
    with pytest.raises(ContentStoreError, match="source_url"):
        service.store(_fetched(url=url), scope=SCOPE, source_url=url)
    _assert_no_side_effects(tmp_path, repo)


def test_an_unstorable_provenance_url_never_echoes_its_content(tmp_path):
    # M-3: the refusal never echoes the URL — whatever defeated parsing may
    # carry a signed query, and this family's messages go to logs. Length
    # only. (Also nothing on disk, nothing in the log: M-1's order.)
    service, repo = _service(tmp_path)
    suspicious = "sign=tokens1234567890 " + "x" * 60  # no scheme, no host
    with pytest.raises(ContentStoreError, match="unparseable provenance") as caught:
        service.store(_fetched(), scope=SCOPE, source_url=suspicious)
    assert "tokens1234567890" not in str(caught.value)
    _assert_no_side_effects(tmp_path, repo)


def test_a_digest_that_is_not_the_vocabulary_is_refused_with_variants(tmp_path):
    service, repo = _service(tmp_path)
    for bad in ["sha256:short", "sha256:" + "AB" * 32, "sha256:" + "0" * 65]:
        with pytest.raises(ContentStoreError, match="untrusted content address"):
            service.read(bad)
    assert repo.rows == []


# --- honest shapes at the edges --------------------------------------------


def test_an_empty_payload_roundtrips(tmp_path):
    # The digest of zero bytes is a valid address; empty content (an empty
    # inline file fetched) must store and read back, not be special-cased.
    service, repo = _service(tmp_path)
    stored = service.store(_fetched(b"", size=0), scope=SCOPE,
                           source_url="https://content.example/empty.bin")
    assert stored.size_bytes == 0
    assert service.read(stored.digest) == b""


def test_a_repository_failure_after_the_blob_is_the_standard_shape(tmp_path):
    # Blob first, row second — a row failure leaves an orphan blob, which is
    # the content-addressed norm: the address is valid, the bytes verified,
    # and the next store of the same digest reuses them without rewriting.
    repo = _FakeRepository()
    repo.fail_next = True
    service = ManifestContentService(repo, tmp_path / "store")
    with pytest.raises(RuntimeError, match="row insert failed"):
        service.store(_fetched(), scope=SCOPE,
                      source_url="https://content.example/a.bin")
    assert _blob_file(tmp_path, BODY_SHA).is_file()
    # And the second store of the same digest succeeds over the orphan.
    repo.fail_next = False
    stored = service.store(_fetched(), scope=SCOPE,
                           source_url="https://content.example/a.bin")
    assert stored.digest == BODY_SHA


def test_a_wrong_sized_existing_blob_is_rewritten_with_the_bytes_in_hand(tmp_path):
    # The dedup shortcut trusts a size match, never a bare existence check:
    # truncation/append corruption under a valid address is healed by the
    # rewrite, at near-zero cost, while the correct bytes are in hand.
    service, _ = _service(tmp_path)
    stored = service.store(_fetched(), scope=SCOPE,
                           source_url="https://content.example/a.bin")
    blob = _blob_file(tmp_path, stored.digest)
    blob.write_bytes(blob.read_bytes()[:-3])  # truncated
    again = service.store(_fetched(), scope=SCOPE,
                          source_url="https://content.example/a.bin")
    assert blob.stat().st_size == len(BODY)
    assert service.read(again.digest) == BODY


# --- provenance: what the audit log may and may not hold -----------------------


@pytest.mark.parametrize("url,expected", [
    ("https://content.example/team/a.bin?sign=x7%2Fsecret&expires=1",
     "https://content.example/team/a.bin"),
    ("https://mirror.example:8443/a.bin",
     "https://mirror.example:8443/a.bin"),
    ("https://cdn.example/deep/path/a.bin",
     "https://cdn.example/deep/path/a.bin"),
    # userinfo and fragment actively dropped, not just "absent by omission":
    # the stored shape is rebuilt from parts, so these can never ride along.
    ("https://token:x7@example.com/a.bin", "https://example.com/a.bin"),
    ("https://example.com/a.bin#section", "https://example.com/a.bin"),
])
def test_stored_urls_carry_path_but_never_query_or_userinfo(tmp_path, url, expected):
    # Query strings are where signed-source tokens live (the fetcher's own
    # log line refuses them for the same reason); the reconciliation anchor
    # is the digest, not a one-time signed URL.
    service, repo = _service(tmp_path)
    service.store(_fetched(url=url), scope=SCOPE, source_url=url)
    assert repo.rows[0].source_url == expected
    assert repo.rows[0].fetched_url == expected


def test_the_two_urls_record_a_redirect_when_there_was_one(tmp_path):
    service, repo = _service(tmp_path)
    service.store(
        _fetched(url="https://cdn.example/final.bin"),
        scope=SCOPE,
        source_url="https://content.example/entry.bin",
    )
    row = repo.rows[0]
    assert row.source_url == "https://content.example/entry.bin"
    assert row.fetched_url == "https://cdn.example/final.bin"


def test_provenance_records_the_credential_name_and_only_the_name(tmp_path):
    # Names, never values: W3's token is ciphertext in another table; the
    # bytes here are the payload and the blob, and the row carries the name
    # an auditor can resolve against W3 if it must.
    service, repo = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE,
                  source_url="https://git.corp/team/a.bin",
                  credential_name="corp-git")
    assert repo.rows[0].credential_name == "corp-git"
    assert service.read(BODY_SHA) == BODY  # the blob is the payload, nothing else


def test_a_credential_less_fetch_stores_null_not_empty(tmp_path):
    service, repo = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    assert repo.rows[0].credential_name is None


def test_a_tz_aware_fetch_time_is_normalized_to_naive_utc(tmp_path):
    service, repo = _service(tmp_path)
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    assert repo.rows[0].fetched_at == datetime(2026, 8, 31, 12, 0, 0)


def test_an_unparseable_provenance_url_is_refused(tmp_path):
    # Garbage must not land in an append-only log that cannot be cleaned up.
    # (the no-trace and no-echo variants live with the width tests above;
    # this one pins the plain refusal itself)
    service, repo = _service(tmp_path)
    with pytest.raises(ContentStoreError, match="unparseable"):
        service.store(_fetched(url="https://content.example/ok"),
                      scope=SCOPE, source_url="not a url at all")
    _assert_no_side_effects(tmp_path, repo)


# --- the root ------------------------------------------------------------------


def test_a_relative_root_resolves_against_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _FakeRepository()
    service = ManifestContentService(repo, Path(DEFAULT_CONTENT_STORE_DIR))
    service.store(_fetched(), scope=SCOPE, source_url="https://content.example/a.bin")
    assert (tmp_path / DEFAULT_CONTENT_STORE_DIR / "blobs").is_dir()


# --- the config seam ------------------------------------------------------------


def test_an_absent_block_yields_the_neutral_default():
    assert content_store_root_from_config({}) == Path(DEFAULT_CONTENT_STORE_DIR)
    assert content_store_root_from_config({"bot_config_manifest": None}) == Path(
        DEFAULT_CONTENT_STORE_DIR
    )


def test_an_overlay_value_wins():
    got = content_store_root_from_config(
        {"bot_config_manifest": {"content_store_dir": "/mnt/nas/manifests"}}
    )
    assert got == Path("/mnt/nas/manifests")


@pytest.mark.parametrize("settings", [
    {"bot_config_manifest": "x"},
    {"bot_config_manifest": {"content_store_dir": 7}},
    {"bot_config_manifest": {"content_store_dir": "  "}},
])
def test_a_malformed_root_is_a_configuration_error(settings):
    # A typo must fail its reader loudly — the alternative is 100-MiB blobs
    # written somewhere nobody chose.
    with pytest.raises(ValueError):
        content_store_root_from_config(settings)


_SHIPPED_APP_YAML = (
    pathlib.Path(__file__).resolve().parents[5]
    / "src" / "agentclaw" / "community" / "configs" / "application.yaml"
)


def test_the_shipped_yaml_carries_the_neutral_default_root():
    # The knob must exist and must ship neutral: a deployment's NAS path
    # appears in its own overlay diff, never in community source.
    tree = yaml.safe_load(_SHIPPED_APP_YAML.read_text(encoding="utf-8"))
    settings = tree["user_config"]
    assert (
        settings["bot_config_manifest"]["content_store_dir"]
        == DEFAULT_CONTENT_STORE_DIR
    )
    assert content_store_root_from_config(settings) == Path(
        DEFAULT_CONTENT_STORE_DIR
    )
