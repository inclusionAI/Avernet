"""Use the existing local bcs-cli; never send an extra chat after creation."""
import json
import re
import subprocess
from pathlib import Path

class BcnAdapter:
    def __init__(self, binary, url, env, diagnostics_dir=None):
        self.binary, self.url, self.env = str(binary), url, env
        self.diagnostics_dir = diagnostics_dir

    def call(self, *args, timeout=30, as_json=True):
        # CLI owns credentials; do not archive stderr (it can contain auth context).
        result = subprocess.run([self.binary, "--url", self.url, "--json", *args], env=self.env,
                                capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        if result.returncode:
            if self.diagnostics_dir:
                # Private run directory only. Never include this file in evaluation evidence.
                path = Path(self.diagnostics_dir) / 'bcs-cli-private.log'
                with path.open('a') as log:
                    log.write(result.stderr + '\n')
                path.chmod(0o600)
            raise RuntimeError(f"BCS CLI {args[0]} failed (exit {result.returncode}); inspect local service health/auth")
        return json.loads(result.stdout) if as_json else result.stdout

    def create(self, members, prompt, topic, entry_member="ceo"):
        if entry_member not in members:
            raise ValueError("Entry member is not in the team")
        # CreateGroup CLI prints labelled fields even with --json. No mutation retries.
        output = self.call("create-group", "--manager", members[entry_member], "--participants",
                           ",".join(v for k, v in members.items() if k != entry_member),
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
