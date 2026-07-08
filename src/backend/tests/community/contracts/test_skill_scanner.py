"""Rule 25 conformance — SkillScannerPlugin.

Consumer under test: ``SkillScanService.start`` (core/skill_center/services/
skill_scan.py). It calls ``scanner.create_sdk()``; when the plugin returns
``None`` (no scanner available) the service stays disabled and ``start()``
returns ``False``.

Plugin-hit assertion: the local ``LocalSkillScanner`` (and community
``NoopSkillScanner``) return ``None`` from ``create_sdk``, so the consumer
short-circuits to ``start() is False`` — proving it reached the plugin.
"""
from __future__ import annotations

from agentclaw.community.core.skill_center.services.skill_scan import SkillScanService


def test_skill_scan_disabled_when_scanner_unavailable(world) -> None:
    svc = world.get(SkillScanService)
    assert svc.start() is False


def test_community_skill_scan_disabled(community_world) -> None:
    svc = community_world.get(SkillScanService)
    assert svc.start() is False
