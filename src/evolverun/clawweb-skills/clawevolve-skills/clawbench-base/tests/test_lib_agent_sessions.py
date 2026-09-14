from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import lib_agent  # noqa: E402


class TranscriptResolutionTests(unittest.TestCase):
    def test_load_transcript_prefers_explicit_session_over_newer_main_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            agent_id = "bench-antchat-glm-5-test"
            session_id = "task_01_123"
            sessions_dir = home / ".openclaw" / "agents" / agent_id / "sessions"
            sessions_dir.mkdir(parents=True)

            explicit_event = {"type": "message", "message": {"role": "assistant", "content": "task"}}
            main_event = {"type": "message", "message": {"role": "assistant", "content": "main"}}
            (sessions_dir / f"{session_id}.jsonl").write_text(
                json.dumps(explicit_event) + "\n",
                encoding="utf-8",
            )
            (sessions_dir / "main-session.jsonl").write_text(
                json.dumps(main_event) + "\n",
                encoding="utf-8",
            )
            (sessions_dir / "sessions.json").write_text(
                json.dumps(
                    {
                        f"agent:{agent_id}:explicit:{session_id}": {
                            "sessionId": session_id,
                            "updatedAt": 100,
                        },
                        f"agent:{agent_id}:main": {
                            "sessionId": "main-session",
                            "updatedAt": 200,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(lib_agent.Path, "home", return_value=home):
                transcript, path = lib_agent._load_transcript(agent_id, session_id, time.time() - 1)

            self.assertEqual(path, sessions_dir / f"{session_id}.jsonl")
            self.assertEqual(transcript, [explicit_event])


class AgentCreationTests(unittest.TestCase):
    def test_agent_add_timeout_continues_when_registry_contains_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            agent_id = "bench-timeout-agent"
            workspace = home / "workspace"
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if cmd == ["openclaw", "agents", "list"]:
                    if len([c for c, _ in calls if c == cmd]) == 1:
                        return lib_agent.subprocess.CompletedProcess(cmd, 0, stdout="Agents:\n- main (default)\n", stderr="")
                    return lib_agent.subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=f"Agents:\n- {agent_id}\n  Workspace: {workspace}\n",
                        stderr="",
                    )
                if cmd[:3] == ["openclaw", "agents", "add"]:
                    raise lib_agent.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="created", stderr="heartbeat")
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(lib_agent.Path, "home", return_value=home), \
                    patch.object(lib_agent.subprocess, "run", side_effect=fake_run):
                created = lib_agent.ensure_agent_exists(agent_id, "antchat/GLM-5", workspace)

            self.assertTrue(created)
            add_calls = [kwargs for cmd, kwargs in calls if cmd[:3] == ["openclaw", "agents", "add"]]
            self.assertEqual(len(add_calls), 1)
            self.assertEqual(add_calls[0]["timeout"], lib_agent.OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS)

    def test_existing_agent_reuses_initial_workspace_parse_without_second_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            agent_id = "bench-existing-agent"
            workspace = home / "workspace"
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if cmd == ["openclaw", "agents", "list"]:
                    return lib_agent.subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=f"Agents:\n- {agent_id}\n  Workspace: {workspace}\n",
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(lib_agent.Path, "home", return_value=home), \
                    patch.object(lib_agent.subprocess, "run", side_effect=fake_run):
                created = lib_agent.ensure_agent_exists(agent_id, "antchat/GLM-5", workspace)

            self.assertFalse(created)
            list_calls = [cmd for cmd, _ in calls if cmd == ["openclaw", "agents", "list"]]
            self.assertEqual(len(list_calls), 1)

    def test_agent_add_retries_with_longer_timeout_when_first_attempt_not_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            agent_id = "bench-retry-agent"
            workspace = home / "workspace"
            list_count = 0
            calls = []

            def fake_run(cmd, **kwargs):
                nonlocal list_count
                calls.append((cmd, kwargs))
                if cmd == ["openclaw", "agents", "list"]:
                    list_count += 1
                    if list_count < 3:
                        return lib_agent.subprocess.CompletedProcess(cmd, 0, stdout="Agents:\n- main (default)\n", stderr="")
                    return lib_agent.subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=f"Agents:\n- {agent_id}\n  Workspace: {workspace}\n",
                        stderr="",
                    )
                if cmd[:3] == ["openclaw", "agents", "add"]:
                    raise lib_agent.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="", stderr="heartbeat")
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(lib_agent.Path, "home", return_value=home), \
                    patch.object(lib_agent.subprocess, "run", side_effect=fake_run):
                created = lib_agent.ensure_agent_exists(agent_id, "antchat/GLM-5", workspace)

            self.assertTrue(created)
            add_calls = [kwargs for cmd, kwargs in calls if cmd[:3] == ["openclaw", "agents", "add"]]
            self.assertEqual(len(add_calls), 2)
            self.assertEqual(add_calls[0]["timeout"], lib_agent.OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS)
            self.assertEqual(add_calls[1]["timeout"], lib_agent.OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS)

    def test_agent_add_raises_when_two_attempts_never_register(self) -> None:
        with tempfile.TemporaryDirectory() as temp_home:
            home = Path(temp_home)
            agent_id = "bench-missing-agent"
            workspace = home / "workspace"
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                if cmd == ["openclaw", "agents", "list"]:
                    return lib_agent.subprocess.CompletedProcess(cmd, 0, stdout="Agents:\n- main (default)\n", stderr="")
                if cmd[:3] == ["openclaw", "agents", "add"]:
                    raise lib_agent.subprocess.TimeoutExpired(cmd, kwargs.get("timeout"), output="", stderr="heartbeat")
                raise AssertionError(f"unexpected command: {cmd}")

            with patch.object(lib_agent.Path, "home", return_value=home), \
                    patch.object(lib_agent.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(lib_agent.OpenClawAgentCreationError):
                    lib_agent.ensure_agent_exists(agent_id, "antchat/GLM-5", workspace)

            add_calls = [kwargs for cmd, kwargs in calls if cmd[:3] == ["openclaw", "agents", "add"]]
            self.assertEqual(len(add_calls), 2)
            self.assertEqual(add_calls[0]["timeout"], lib_agent.OPENCLAW_AGENTS_ADD_FIRST_TIMEOUT_SECONDS)
            self.assertEqual(add_calls[1]["timeout"], lib_agent.OPENCLAW_AGENTS_ADD_RETRY_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()

class SingleboxProfileTests(unittest.TestCase):
    def test_transcript_uses_selected_bot_not_home_profile(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); selected = base / "bot/openclaw"; sessions = selected / "agents/bench-test/sessions"
            sessions.mkdir(parents=True); (selected / "workspace").mkdir()
            event = {"type": "message", "message": {"role": "assistant", "content": "selected bot"}}
            transcript = sessions / "task-test.jsonl"; transcript.write_text(json.dumps(event) + "\n")
            with patch.dict(os.environ, {"CLAWWEB_VERSION": "openversion", "OPENCLAW_STATE_DIR": str(selected), "OPENCLAW_WORKSPACE": str(selected / "workspace")}):
                data, path = lib_agent._load_transcript("bench-test", "task-test", time.time() - 1)
                self.assertEqual(path, transcript.resolve())
                self.assertEqual(data, [event])
                self.assertEqual(lib_agent._openclaw_workspace(), (selected / "workspace").resolve())
                with self.assertRaises(RuntimeError): lib_agent._get_agent_store_dir("../outside")

    def test_internal_default_and_missing_local_profile(self):
        import os
        with patch.dict(os.environ, {"CLAWWEB_VERSION": "internalversion", "OPENCLAW_STATE_DIR": "/unused"}):
            self.assertEqual(lib_agent._openclaw_state_root(), Path.home() / ".openclaw")
        with patch.dict(os.environ, {"CLAWWEB_VERSION": "openversion", "OPENCLAW_STATE_DIR": ""}):
            with self.assertRaises(RuntimeError): lib_agent._openclaw_state_root()
