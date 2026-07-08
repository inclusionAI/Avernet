"""Unit tests for api/publish_manage/_enums.py — Publish workflow enums."""

from enum import StrEnum

from secbaas.api.publish_manage import (
    BatchStatus,
    PublishEventType,
    PublishRecordResult,
    PublishStage,
    PublishStatus,
    PublishType,
    RestartScope,
)


class TestPublishType:
    """Tests for PublishType StrEnum."""

    def test_values(self):
        assert PublishType.CREATE == "CREATE"
        assert PublishType.UPDATE == "UPDATE"
        assert PublishType.RESTART == "RESTART"
        assert PublishType.SCALE_UP == "SCALE_UP"
        assert PublishType.SCALE_DOWN == "SCALE_DOWN"
        assert PublishType.DESTROY == "DESTROY"

    def test_is_str_enum(self):
        assert issubclass(PublishType, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in PublishType]
        assert len(values) == len(set(values))


class TestPublishStage:
    """Tests for PublishStage StrEnum."""

    def test_values(self):
        assert PublishStage.PREPUB == "PREPUB"
        assert PublishStage.GRAY == "GRAY"
        assert PublishStage.PROD_FIRST_BATCH == "PROD_FIRST_BATCH"
        assert PublishStage.PROD_OTHER_BATCH == "PROD_OTHER_BATCH"
        assert PublishStage.SUCCESS == "SUCCESS"

    def test_is_str_enum(self):
        assert issubclass(PublishStage, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in PublishStage]
        assert len(values) == len(set(values))


class TestPublishStatus:
    """Tests for PublishStatus StrEnum."""

    def test_values(self):
        assert PublishStatus.PENDING == "PENDING"
        assert PublishStatus.ACTIVE == "ACTIVE"
        assert PublishStatus.APPROVING == "APPROVING"
        assert PublishStatus.REJECTED == "REJECTED"
        assert PublishStatus.FAILED == "FAILED"
        assert PublishStatus.SUCCESS == "SUCCESS"
        assert PublishStatus.REVOKED == "REVOKED"

    def test_is_str_enum(self):
        assert issubclass(PublishStatus, StrEnum)

    def test_str_comparison(self):
        assert PublishStatus.PENDING == "PENDING"

    def test_unique_values(self):
        values = [s.value for s in PublishStatus]
        assert len(values) == len(set(values))


class TestBatchStatus:
    """Tests for BatchStatus StrEnum."""

    def test_values(self):
        assert BatchStatus.PENDING == "PENDING"
        assert BatchStatus.RUNNING == "RUNNING"
        assert BatchStatus.COMPLETED == "COMPLETED"
        assert BatchStatus.FAILED == "FAILED"
        assert BatchStatus.ROLLED_BACK == "ROLLED_BACK"

    def test_is_str_enum(self):
        assert issubclass(BatchStatus, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in BatchStatus]
        assert len(values) == len(set(values))


class TestPublishEventType:
    """Tests for PublishEventType StrEnum."""

    def test_values(self):
        assert PublishEventType.CREATE == "CREATE"
        assert PublishEventType.DESTROY == "DESTROY"
        assert PublishEventType.RESTART == "RESTART"
        assert PublishEventType.UPDATE == "UPDATE"
        assert PublishEventType.START == "START"
        assert PublishEventType.STOP == "STOP"

    def test_is_str_enum(self):
        assert issubclass(PublishEventType, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in PublishEventType]
        assert len(values) == len(set(values))


class TestPublishRecordResult:
    """Tests for PublishRecordResult StrEnum."""

    def test_values(self):
        assert PublishRecordResult.PENDING == "PENDING"
        assert PublishRecordResult.PROCESSING == "PROCESSING"
        assert PublishRecordResult.SUCCESS == "SUCCESS"
        assert PublishRecordResult.FAILED == "FAILED"

    def test_is_str_enum(self):
        assert issubclass(PublishRecordResult, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in PublishRecordResult]
        assert len(values) == len(set(values))


class TestRestartScope:
    """Tests for RestartScope StrEnum."""

    def test_values(self):
        assert RestartScope.ALL == "all"
        assert RestartScope.UNHEALTHY == "unhealthy"

    def test_is_str_enum(self):
        assert issubclass(RestartScope, StrEnum)

    def test_unique_values(self):
        values = [v.value for v in RestartScope]
        assert len(values) == len(set(values))
