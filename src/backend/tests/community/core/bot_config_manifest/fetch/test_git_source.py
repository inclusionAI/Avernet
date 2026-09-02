"""Tests for git sources (``fetch/git_source.py``, W7, #1475).

Two kinds of tests live here, and the split is deliberate:

* **argv-shape tests** inject a fake runner and assert what the git CLI is
  asked to do — scheme guard, credential placement, no prompts. They exist
  because the https wire itself is not reachable from a test environment,
  and because "where the credential travels" is a property of the argv, not
  of the network.
* **behaviour tests** run real ``git`` subprocesses against bare repos
  created on disk by the fixtures — the same local-repo precedent as the
  schema suite's archive fixtures, and the only honest way to test that a
  shallow fetch, ref resolution and tree enumeration really work.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    ARCHIVE_MEMBER_LIMIT,
    FETCH_ENTRY_LIMITS,
    GIT_CHECKOUT_MEMBER_LIMIT,
    GIT_CHECKOUT_UNPACKED_LIMIT,
    GIT_FETCH_TIMEOUT_S,
    GIT_SINGLE_FILE_LIMIT,
)


def test_git_limits_align_with_the_archives_they_generalise():
    # A checkout is the same class of hazard as an unpacked archive: the
    # numbers align rather than invent a second dialect that drifts.
    assert GIT_CHECKOUT_UNPACKED_LIMIT == FETCH_ENTRY_LIMITS["resources_unpacked"]
    assert GIT_CHECKOUT_MEMBER_LIMIT == ARCHIVE_MEMBER_LIMIT
    assert GIT_SINGLE_FILE_LIMIT == FETCH_ENTRY_LIMITS["skills"]
    assert GIT_FETCH_TIMEOUT_S > 0
