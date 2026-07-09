"""Tests for Resource domain model properties and factory functions."""
from __future__ import annotations

from datetime import datetime

import pytest

from agentclaw.community.core.resources.models import (
    Resource,
    create_file_resource,
    create_node_resource,
    create_url_resource,
)


# ---------------------------------------------------------------------------
# FILE resource
# ---------------------------------------------------------------------------


class TestFileResource:
    @pytest.fixture
    def file_res(self) -> Resource:
        return create_file_resource(
            name="report.pdf",
            path="/data/report.pdf",
            parent_path="/data",
            size=1024,
            mime_type="application/pdf",
            extension="pdf",
            is_directory=False,
            content_hash="abc123",
            preview_available=True,
            bolt_id="bot1",
        )

    def test_is_file_true(self, file_res):
        assert file_res.is_file is True

    def test_is_url_false_for_file(self, file_res):
        assert file_res.is_url is False

    def test_is_node_false_for_file(self, file_res):
        assert file_res.is_node is False

    def test_path_property(self, file_res):
        assert file_res.path == "/data/report.pdf"

    def test_parent_path_property(self, file_res):
        assert file_res.parent_path == "/data"

    def test_size_property(self, file_res):
        assert file_res.size == 1024

    def test_mime_type_property(self, file_res):
        assert file_res.mime_type == "application/pdf"

    def test_extension_property(self, file_res):
        assert file_res.extension == "pdf"

    def test_is_directory_false(self, file_res):
        assert file_res.is_directory is False

    def test_content_hash_property(self, file_res):
        assert file_res.content_hash == "abc123"

    def test_preview_available_true(self, file_res):
        assert file_res.preview_available is True

    def test_path_setter(self, file_res):
        file_res.path = "/new/path.pdf"
        assert file_res.attributes["path"] == "/new/path.pdf"

    def test_size_setter(self, file_res):
        file_res.size = 2048
        assert file_res.attributes["size"] == 2048

    def test_mime_type_setter(self, file_res):
        file_res.mime_type = "text/plain"
        assert file_res.attributes["mime_type"] == "text/plain"

    def test_extension_setter(self, file_res):
        file_res.extension = "txt"
        assert file_res.attributes["extension"] == "txt"

    def test_is_directory_setter(self, file_res):
        file_res.is_directory = True
        assert file_res.attributes["is_directory"] is True

    def test_content_hash_setter(self, file_res):
        file_res.content_hash = "new_hash"
        assert file_res.attributes["content_hash"] == "new_hash"

    def test_preview_available_setter(self, file_res):
        file_res.preview_available = False
        assert file_res.attributes["preview_available"] is False

    def test_parent_path_setter(self, file_res):
        file_res.parent_path = "/other"
        assert file_res.attributes["parent_path"] == "/other"

    def test_non_file_path_returns_none(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.path is None

    def test_non_file_size_returns_zero(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.size == 0

    def test_non_file_mime_type_returns_none(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.mime_type is None

    def test_non_file_extension_returns_none(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.extension is None

    def test_non_file_is_directory_returns_false(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.is_directory is False

    def test_non_file_content_hash_returns_none(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.content_hash is None

    def test_non_file_preview_available_returns_false(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        assert url_res.preview_available is False

    def test_setters_noop_on_non_file(self):
        url_res = create_url_resource(name="site", url="http://example.com")
        # Setting file properties on a URL resource should be a no-op
        url_res.path = "/should/not/set"
        assert "path" not in url_res.attributes or url_res.attributes.get("path") != "/should/not/set"


# ---------------------------------------------------------------------------
# URL resource
# ---------------------------------------------------------------------------


class TestUrlResource:
    @pytest.fixture
    def url_res(self) -> Resource:
        return create_url_resource(
            name="api-endpoint",
            url="https://api.example.com/data",
            method="POST",
            headers={"Authorization": "Bearer tok"},
            parent_path="/apis",
        )

    def test_is_url_true(self, url_res):
        assert url_res.is_url is True

    def test_is_file_false(self, url_res):
        assert url_res.is_file is False

    def test_url_property(self, url_res):
        assert url_res.url == "https://api.example.com/data"

    def test_method_property(self, url_res):
        assert url_res.method == "POST"

    def test_headers_property(self, url_res):
        assert url_res.headers == {"Authorization": "Bearer tok"}

    def test_url_setter(self, url_res):
        url_res.url = "https://new.example.com"
        assert url_res.attributes["url"] == "https://new.example.com"

    def test_method_setter(self, url_res):
        url_res.method = "GET"
        assert url_res.attributes["method"] == "GET"

    def test_headers_setter(self, url_res):
        url_res.headers = {"X-Custom": "value"}
        assert url_res.attributes["headers"] == {"X-Custom": "value"}

    def test_last_fetch_status_none_by_default(self, url_res):
        assert url_res.last_fetch_status is None

    def test_last_fetch_status_setter(self, url_res):
        url_res.last_fetch_status = 200
        assert url_res.attributes["last_fetch_status"] == 200

    def test_last_fetch_time_none_by_default(self, url_res):
        assert url_res.last_fetch_time is None

    def test_last_fetch_time_setter_and_getter(self, url_res):
        now = datetime(2024, 1, 15, 12, 0, 0)
        url_res.last_fetch_time = now
        assert url_res.last_fetch_time == now

    def test_last_fetch_time_from_string(self, url_res):
        url_res.attributes["last_fetch_time"] = "2024-01-15T12:00:00"
        result = url_res.last_fetch_time
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_content_type_setter(self, url_res):
        url_res.content_type = "application/json"
        assert url_res.attributes["content_type"] == "application/json"

    def test_cached_content_path_setter(self, url_res):
        url_res.cached_content_path = "/cache/api-endpoint.json"
        assert url_res.attributes["cached_content_path"] == "/cache/api-endpoint.json"

    def test_non_url_returns_none_for_url(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.url is None

    def test_non_url_method_returns_get(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.method == "GET"

    def test_non_url_headers_returns_none(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.headers is None

    def test_non_url_last_fetch_status_returns_none(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.last_fetch_status is None

    def test_non_url_last_fetch_time_returns_none(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.last_fetch_time is None

    def test_non_url_content_type_returns_none(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.content_type is None

    def test_non_url_cached_content_path_returns_none(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        assert file_res.cached_content_path is None

    def test_url_setters_noop_on_non_url(self):
        file_res = create_file_resource(name="f", path="/f.txt")
        file_res.url = "http://should-not-set.com"
        assert file_res.attributes.get("url") != "http://should-not-set.com"


# ---------------------------------------------------------------------------
# NODE resource
# ---------------------------------------------------------------------------


class TestNodeResource:
    @pytest.fixture
    def node_res(self) -> Resource:
        return create_node_resource(
            name="my-node",
            node_address="10.0.0.1:8080",
            path_alias="node-alias",
            scan_recursive=True,
            parent_path="/nodes",
        )

    def test_is_node_true(self, node_res):
        assert node_res.is_node is True

    def test_is_file_false(self, node_res):
        assert node_res.is_file is False

    def test_node_address_property(self, node_res):
        assert node_res.node_address == "10.0.0.1:8080"

    def test_path_alias_property(self, node_res):
        assert node_res.path_alias == "node-alias"

    def test_scan_recursive_default_true(self, node_res):
        assert node_res.scan_recursive is True

    def test_node_address_setter(self, node_res):
        node_res.node_address = "10.0.0.2:9090"
        assert node_res.attributes["node_address"] == "10.0.0.2:9090"

    def test_path_alias_setter(self, node_res):
        node_res.path_alias = "new-alias"
        assert node_res.attributes["path_alias"] == "new-alias"

    def test_scan_recursive_setter(self, node_res):
        node_res.scan_recursive = False
        assert node_res.attributes["scan_recursive"] is False

    def test_file_count_default_zero(self, node_res):
        assert node_res.file_count == 0

    def test_file_count_setter(self, node_res):
        node_res.file_count = 42
        assert node_res.attributes["file_count"] == 42

    def test_last_scan_time_none_by_default(self, node_res):
        assert node_res.last_scan_time is None

    def test_last_scan_time_setter_and_getter(self, node_res):
        t = datetime(2024, 3, 10, 8, 0, 0)
        node_res.last_scan_time = t
        assert node_res.last_scan_time == t

    def test_last_scan_time_from_string(self, node_res):
        node_res.attributes["last_scan_time"] = "2024-03-10T08:00:00"
        result = node_res.last_scan_time
        assert isinstance(result, datetime)

    def test_path_alias_defaults_to_name_when_none(self):
        res = create_node_resource(name="mynode", node_address="10.0.0.1:80", path_alias=None)
        assert res.path_alias == "mynode"

    def test_non_node_returns_none_for_node_address(self):
        url_res = create_url_resource(name="u", url="http://x.com")
        assert url_res.node_address is None

    def test_non_node_scan_recursive_returns_true(self):
        url_res = create_url_resource(name="u", url="http://x.com")
        assert url_res.scan_recursive is True

    def test_non_node_file_count_returns_zero(self):
        url_res = create_url_resource(name="u", url="http://x.com")
        assert url_res.file_count == 0

    def test_non_node_last_scan_time_returns_none(self):
        url_res = create_url_resource(name="u", url="http://x.com")
        assert url_res.last_scan_time is None


# ---------------------------------------------------------------------------
# to_dict / round-trip
# ---------------------------------------------------------------------------


class TestResourceToDict:
    def test_to_dict_contains_required_keys(self):
        res = create_url_resource(name="api", url="http://x.com")
        d = res.to_dict()
        for key in ["id", "name", "resource_type", "status", "attributes", "bolt_id"]:
            assert key in d

    def test_resource_type_is_string_in_dict(self):
        res = create_file_resource(name="f", path="/f.txt")
        d = res.to_dict()
        assert d["resource_type"] == "file"

    def test_bolt_id_defaults_to_default_string(self):
        res = create_url_resource(name="u", url="http://x.com")
        assert res.to_dict()["bolt_id"] == "default"

    def test_bolt_id_none_becomes_default_in_dict(self):
        res = create_url_resource(name="u", url="http://x.com")
        res.bolt_id = None
        assert res.to_dict()["bolt_id"] == "default"
