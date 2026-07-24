"""Conformance test for the SchemaCatalog SPI (Rule 25)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gateway.community.plugins.schema_catalog.bare import BareSchemaCatalog
from gateway.community.spi.schema_catalog import SchemaCatalog


def _write(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_reads_committed_file(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, {"openapi": "3.1.0", "paths": {"/openapi/v1/bots": {}}})
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()
    served: SchemaCatalog = catalog  # BareSchemaCatalog satisfies the protocol
    assert served.current("bots")["openapi"] == "3.1.0"


def test_unknown_domain_returns_empty(tmp_path: Path) -> None:
    catalog = BareSchemaCatalog({})
    assert catalog.current("bots") == {}


def test_refresh_adopts_changed_file(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, {"version": 1})
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()
    assert catalog.current("bots") == {"version": 1}

    _write(src, {"version": 2})
    assert catalog.refresh("bots") is True
    assert catalog.current("bots") == {"version": 2}


def test_keeps_last_known_good_on_malformed(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, {"version": 1})
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()

    src.write_text("{not valid json", encoding="utf-8")
    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {"version": 1}  # unchanged


def test_keeps_last_known_good_on_missing_file(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, {"version": 1})
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()

    src.unlink()
    assert catalog.refresh("bots") is False
    assert catalog.current("bots") == {"version": 1}


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, [1, 2, 3])  # a list, not an object
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()
    assert catalog.current("bots") == {}


async def test_refresh_loop_adopts_then_stops(tmp_path: Path) -> None:
    src = tmp_path / "bots.openapi.json"
    _write(src, {"version": 1})
    catalog = BareSchemaCatalog({"bots": src})
    catalog.refresh_all()

    stop = asyncio.Event()
    task = asyncio.create_task(catalog.refresh_loop(0.01, stop))
    _write(src, {"version": 2})
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert catalog.current("bots") == {"version": 2}
