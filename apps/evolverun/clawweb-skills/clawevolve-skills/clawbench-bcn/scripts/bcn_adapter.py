"""Use the existing local bcs-cli; never send an extra chat after creation."""
import json
import re
import subprocess
import time

class BcnAdapter:
    def __init__(self, binary, url, env):
        self.binary, self.url, self.env = str(binary), url, env

    def call(self, *args, timeout=30, as_json=True):
        # CLI owns credentials; do not archive stderr (it can contain auth context).
        result = subprocess.run([self.binary, "--url", self.url, "--json", *args], env=self.env,
                                capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(f"BCS CLI {args[0]} failed (exit {result.returncode}); inspect local service health/auth")
        return json.loads(result.stdout) if as_json else result.stdout

    def create(self, members, prompt, topic):
        # CreateGroup CLI prints labelled fields even with --json. No mutation retries.
        output = self.call("create-group", "--manager", members["ceo"], "--participants",
                           ",".join(v for k, v in members.items() if k != "ceo"),
                           "--context", prompt, "--topic", topic, as_json=False)
        group = re.search(r"^\s*ID: (\S+)\s*$", output, re.M)
        session = re.search(r"^\s*Session: (\S+)\s*$", output, re.M)
        if not group or not session:
            raise RuntimeError("Create result uncertain: inspect BCS before retry; no automatic resubmission")
        if not session[1].startswith(group[1] + ":"):
            raise RuntimeError("Created session does not belong to group")
        return group[1], session[1]

    def get(self, session, timeout=30):
        data = self.call("session", "get", session, timeout=timeout)
        if not isinstance(data, dict):
            raise ValueError("Invalid Session response")
        return data

    def wait(self, session, deadline, on_state):
        while time.monotonic() < deadline:
            try:
                state = self.get(session, timeout=max(.1, min(30, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    return "timeout", ""
                raise RuntimeError("BCS read timed out before task deadline")
            on_state(state)
            status = state.get("status")
            if status == "completed":
                if state.get("error_message"):
                    return "failed", ""
                output = state.get("output")
                if not isinstance(output, str) or not output.strip():
                    return "failed", ""
                return "completed", output
            if status != "running":
                raise ValueError(f"Unsupported session status: {status}")
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        return "timeout", ""
