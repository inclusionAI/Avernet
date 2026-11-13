"""Source repair accepts a plain record and an injected atomic publisher."""

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Record:
    revision: int
    text: str


class Publisher:
    def __init__(self):
        self.record = Record(1, "plain text")
        self.documents = ["old"]
        self.owner = ""
        self.released = []

    def read(self, source_id):
        return self.record

    def claim_preparation(
        self, source_id, revision, key, token, seconds, *, force=False
    ):
        assert force
        if self.owner:
            return False
        self.owner, self.revision = token, revision
        return True

    def renew_preparation(self, *args):
        return True

    def publish_documents(self, documents, key, token):
        if self.record.revision != self.revision or token != self.owner:
            return False
        self.documents = documents
        self.owner = ""
        return True

    def release_preparation(self, source_id, revision, key, token, error):
        self.released.append(error)
        self.owner = ""


def test_source_repair_does_not_require_a_card_or_change_business_revision():
    from search_core.core.source_rebuild import rebuild_source

    repo = Publisher()
    result = rebuild_source(
        repo,
        "document",
        index_key="existing",
        read_source=repo.read,
        prepare_documents=lambda row: [row.text.upper()],
    )
    assert result == {"published": True, "index_key": "existing", "saved_revision": 1}
    assert repo.documents == ["PLAIN TEXT"]
    assert repo.record.revision == 1


@pytest.mark.parametrize("changed", [False, True])
def test_failed_or_stale_conversion_keeps_previous_documents(changed):
    from search_core.api.index_builds import IndexBuildConflict
    from search_core.core.source_rebuild import rebuild_source

    repo = Publisher()

    def prepare(row):
        if changed:
            repo.record = Record(2, "updated")
            return ["stale"]
        raise OSError("conversion failed")

    with pytest.raises(IndexBuildConflict if changed else OSError):
        rebuild_source(
            repo,
            "document",
            index_key="existing",
            read_source=repo.read,
            prepare_documents=prepare,
        )
    assert repo.documents == ["old"]
    assert repo.released == ["REBUILD_FAILED"]
