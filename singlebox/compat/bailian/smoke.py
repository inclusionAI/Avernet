"""Run one real engine/file-read/model round trip on a GitHub-hosted runner."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess

from configure import ENGINES


def diagnostic_excerpt(text: str, key: str) -> str:
    text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)(?:sk-|ghp_|github_pat_)[a-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", text)
    return text[-4000:]


def command(engine: str, prompt: str, root: Path) -> list[str]:
    if engine == "openclaw":
        return ["openclaw", "agent", "--local", "--agent", "main", "--session-id",
                "bailian-smoke", "--message", prompt, "--json", "--timeout", "180"]
    if engine == "deepseek-harness":
        return ["dsh", "--profile", "headless", prompt]
    if engine == "claude-code":
        return ["claude", "-p", prompt, "--output-format", "text", "--max-turns", "4",
                "--allowedTools", "Read"]
    return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            "--output-last-message", str(root / "final.txt"), prompt]


def answer(engine: str, stdout: str, root: Path) -> str:
    if engine == "codex":
        return (root / "final.txt").read_text(encoding="utf-8")
    if engine == "openclaw":
        result = json.loads(stdout)
        return "\n".join(item.get("text", "") for item in result.get("payloads", []))
    return stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=ENGINES, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.profile.resolve()
    report = {"engine": args.engine, "scope": "engine_model_file_read",
              "bcs_certified": False, "commit": os.environ.get("GITHUB_SHA", ""),
              "status": "UNKNOWN", "reason": "not_run"}
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise ValueError("Live engine execution requires a GitHub-hosted Actions runner")
        key = os.environ.get("BAILIAN_API_KEY", "").strip()
        if not key:
            raise ValueError("Missing Actions Secret BAILIAN_API_KEY")
        report["model"] = json.loads((root / "profile.json").read_text())["model"]
        env = os.environ.copy()
        env.update({"BAILIAN_API_KEY": key,
                    "OPENCLAW_STATE_DIR": str(root / "openclaw"),
                    "OPENCLAW_CONFIG_PATH": str(root / "openclaw/openclaw.json"),
                    "DSH_HOME": str(root / "dsh"),
                    "CLAUDE_CONFIG_DIR": str(root / "claude"),
                    "ANTHROPIC_AUTH_TOKEN": key,
                    "DISABLE_AUTOUPDATER": "1"})
        # Config selection is passed only to the child process, never this session.
        env["CODEX_HOME"] = str(root / "codex")
        env.pop("ANTHROPIC_API_KEY", None)
        marker = "BCS_" + secrets.token_hex(16)
        (root / "workspace/probe.txt").write_text(marker + "\n", encoding="utf-8")
        prompt = "Use your file-reading tool to read probe.txt in the current workspace. Reply with only its complete content."
        with subprocess.Popen(command(args.engine, prompt, root), env=env,
                              cwd=root / "workspace", stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=240)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise ValueError("Engine exceeded 240-second timeout") from None
        report["exit_code"] = proc.returncode
        if proc.returncode:
            # Persist no raw engine logs: upstream diagnostics can contain credentials.
            report.update(status="FAIL", reason="engine_exit_nonzero")
            diagnostic = (stdout + stderr).lower()
            if any(s in diagnostic for s in ("401", "403", "429", "quota", "rate_limit")):
                report.update(status="UNKNOWN", reason="provider_auth_or_quota")
            print(json.dumps({"diagnostic": diagnostic_excerpt(stderr, key)}))
        elif marker not in answer(args.engine, stdout, root):
            report.update(status="FAIL", reason="file_content_not_returned")
            print(json.dumps({"diagnostic": diagnostic_excerpt(
                stderr + "\nFinal answer: " + answer(args.engine, stdout, root), key)}))
        else:
            report.update(status="PASS", reason="random_file_content_returned")
    except (ValueError, OSError) as error:
        # Only controlled errors above are safe to expose; never dump exception payloads.
        report.update(status="UNKNOWN", reason=type(error).__name__)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
