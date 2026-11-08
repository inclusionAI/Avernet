"""Generate isolated, credential-free engine profiles for the live CI smoke."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


ENGINES = ("openclaw", "deepseek-harness", "claude-code", "codex")


def configure(root: Path, base_url: str, model: str) -> dict:
    base_url = base_url.rstrip("/")
    url = urlsplit(base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path != "/compatible-mode/v1"):
        raise ValueError("Expected an HTTPS Bailian /compatible-mode/v1 base URL without credentials")
    if not model or any(ord(c) < 32 for c in model):
        raise ValueError("Model must be a nonempty model ID")
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    anthropic_url = base_url.removesuffix("/compatible-mode/v1") + "/apps/anthropic"

    def write(name: str, data: object) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    write("openclaw/openclaw.json", {
        "models": {"mode": "replace", "providers": {"bailian": {
            "baseUrl": base_url, "apiKey": "${BAILIAN_API_KEY}",
            "api": "openai-completions",
            "models": [{"id": model, "name": model, "input": ["text"],
                        "contextWindow": 1000000, "maxTokens": 4096}],
        }}},
        "agents": {"defaults": {
            "model": {"primary": "bailian/" + model},
            "workspace": str(root / "workspace"),
        }},
        "gateway": {"mode": "local"},
        "tools": {"profile": "coding"},
    })
    # JSON is a YAML subset accepted by Cordis; no runtime credentials are saved.
    write("dsh/profiles/headless/cordis.patch.yml", [
        {"id": "llm-pi-ai", "config": {"providers": {"bailian": {
            "apiKeyEnv": "BAILIAN_API_KEY", "api": "openai-completions",
            "baseURL": base_url,
            "compat": {"supportsDeveloperRole": False, "maxTokensField": "max_tokens"},
            "models": [{"id": model, "input": ["text"],
                        "contextWindow": 1000000, "maxTokens": 4096}],
        }}}},
        {"id": "agent-default-model", "config": {"provider": "bailian", "model": model}},
    ])
    write("claude/settings.json", {"env": {
        "ANTHROPIC_BASE_URL": anthropic_url,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
        "DISABLE_AUTOUPDATER": "1",
    }})
    (root / "codex").mkdir()
    (root / "codex/config.toml").write_text(
        f'model = {json.dumps(model)}\nmodel_provider = "bailian"\n'
        '[model_providers.bailian]\nname = "Bailian"\n'
        f'base_url = {json.dumps(base_url)}\n'
        'env_key = "BAILIAN_API_KEY"\nwire_api = "responses"\n'
        'request_max_retries = 1\nstream_max_retries = 1\n', encoding="utf-8")
    (root / "workspace").mkdir()
    metadata = {"model": model, "engines": list(ENGINES), "status": "CONFIGURED_NOT_VERIFIED"}
    write("profile.json", metadata)
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="qwen3.8-flash")
    args = parser.parse_args()
    configure(args.output.resolve(), args.base_url, args.model)
