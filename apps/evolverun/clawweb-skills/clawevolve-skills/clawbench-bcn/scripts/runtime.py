"""Singlebox adapter; only operate on this run's unique Profile copy."""
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
import yaml
from task_loader import MEMBERS

def local_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("P1 BCS URL must be loopback without embedded credentials")
    return url.rstrip("/")

def default_runtime():
    repo = next((p for p in Path(__file__).resolve().parents if (p / "singlebox/singlebox.sh").is_file()), None)
    if repo is None:
        raise ValueError("Run this Skill from an Avernet checkout, or specify --runtime")
    model_file = Path(os.environ.get("SINGLEBOX_MODEL_CONFIG_FILE", str(repo / "singlebox/.dependencies/openclaw/model-config/openclaw.json"))).expanduser()
    if not model_file.is_file():
        raise ValueError("Singlebox model configuration missing; configure Singlebox first, or specify --runtime")
    data = json.loads(model_file.read_text())
    primary = data.get("agents", {}).get("defaults", {}).get("model", {})
    primary = primary.get("primary") if isinstance(primary, dict) else primary
    if not isinstance(primary, str) or "/" not in primary:
        raise ValueError("Singlebox primary model missing")
    provider_id, model = primary.split("/", 1)
    provider = data.get("models", {}).get("providers", {}).get(provider_id, {})
    if provider_id == "singlebox-mock":
        raise ValueError("Bench requires a real Singlebox model, not mock")
    if provider.get("api") not in ("openai-completions", "openai"):
        raise ValueError("Default Judge requires an OpenAI-compatible model; specify --runtime for a separate Judge")
    key = provider.get("apiKey")
    if isinstance(key, str) and key.startswith("${") and key.endswith("}"):
        key = os.environ.get(key[2:-1])
    if not isinstance(key, str) or not key:
        raise ValueError("Singlebox Judge credential unavailable; provide its referenced environment variable or --runtime")
    # In-memory only; do not generate a Runtime YAML or expose credentials in logs.
    os.environ["BCN_BENCH_DEFAULT_JUDGE_KEY"] = key
    return {
        "repository": str(repo), "output_dir": str(Path.home() / ".cache/clawbench-bcn-p1/runs"),
        "bcs_url": "http://127.0.0.1:21000", "model_mode": "reuse",
        "port_start": 32001, "startup_timeout_seconds": 300,
        "judge": {"url": provider.get("baseUrl", "").rstrip("/") + "/chat/completions",
                  "model": model, "api_key_env": "BCN_BENCH_DEFAULT_JUDGE_KEY", "timeout_seconds": 120},
    }

def load_runtime(path=None):
    config = yaml.safe_load(Path(path).read_text()) if path else default_runtime()
    required = {"repository", "output_dir", "bcs_url", "model_mode", "port_start", "startup_timeout_seconds", "judge"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("Runtime fields must match the supplied example")
    for key in ("repository", "output_dir"):
        config[key] = Path(config[key]).expanduser().resolve()
    repo = config["repository"]
    if not (repo / "singlebox/singlebox.sh").is_file() or not (repo / "apps/bcs/target/debug/bcs-cli").is_file():
        raise ValueError("Prepare Singlebox and build bcs-cli first")
    # Singlebox sources .env.local after inheriting the environment. Reject
    # explicit overrides of our owned runtime roots instead of editing that file.
    local_env = repo / "singlebox/.env.local"
    if local_env.is_file() and re.search(r"(?m)^\s*(?:export\s+)?(?:STANDALONE_OPENCLAW_ROOT|OPENCLAW_PROFILE_ROOT|OPENCLAW_WORKSPACE_ROOT)=", local_env.read_text()):
        raise ValueError("singlebox/.env.local overrides runtime roots; review before running Bench")
    config["bcs_url"] = local_url(config["bcs_url"])
    if config["model_mode"] not in ("home", "manual", "reuse"):
        raise ValueError("Real model mode required: home or manual")
    if type(config["port_start"]) is not int or not 1024 <= config["port_start"] <= 65495:
        raise ValueError("Invalid port_start")
    if not isinstance(config["judge"], dict):
        raise ValueError("Judge config must be a mapping")
    for timeout in (config["startup_timeout_seconds"], config["judge"].get("timeout_seconds")):
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise ValueError("Timeout must be within (0,3600]")
    judge = config["judge"]
    if set(judge) != {"url", "model", "api_key_env", "timeout_seconds"}:
        raise ValueError("Invalid Judge config")
    url = urlsplit(judge["url"])
    if url.username or url.password or url.query or url.fragment or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))):
        raise ValueError("Judge requires HTTPS (or loopback HTTP), without credentials in URL")
    if not judge["model"] or not os.environ.get(judge["api_key_env"]):
        raise ValueError("Judge model or API key environment variable missing")
    return config

def validate_profile(source):
    source = Path(source).resolve()
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError("Profile symlinks are not supported")
    manifest = json.loads((source / "bots.json").read_text())
    bots = manifest.get("bots", [])
    if len(bots) != 5 or {b["profile"] for b in bots} != MEMBERS:
        raise ValueError("P1 requires the Singlebox five-member profile")
    for bot in bots:
        name = bot["source"]
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Unsafe profile source")
        if bot.get("runtime", {}).get("type", "openclaw") != "openclaw":
            raise ValueError("P1 only supports OpenClaw members")
        for file in ("AGENTS.md", "IDENTITY.md", "KNOWLEDGE.md"):
            if not (source / name / file).is_file():
                raise ValueError(f"Missing profile file {name}/{file}")
    return manifest

class Singlebox:
    def __init__(self, config, source, run_dir):
        self.config, self.run_dir = config, Path(run_dir)
        self.root = self.run_dir / "openclaw"
        self.profile = self.run_dir / "team"
        manifest = validate_profile(source)
        if self.run_dir.resolve().is_relative_to(Path(source).resolve()):
            raise ValueError("Output must be outside Profile source")
        shutil.copytree(source, self.profile)
        self.mapping = {}
        self.ports = []
        digest = hashlib.sha256()
        for file in sorted(Path(source).rglob("*")):
            if file.is_file():
                digest.update(str(file.relative_to(source)).encode()); digest.update(file.read_bytes())
        self.digest = digest.hexdigest()
        for index, bot in enumerate(manifest["bots"]):
            alias = bot["profile"]
            name = f"bench-{self.run_dir.name}-{alias}"
            bot["profile"] = name
            # Singlebox uses name as BCN botId as well as display name.
            bot["name"] = f"{bot['name']} [{self.run_dir.name}]"
            self.mapping[alias] = name
            port = config["port_start"] + index * 10
            self.ports.append(port)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        manifest["port_start"], manifest["port_step"] = config["port_start"], 10
        (self.profile / "bots.json").write_text(json.dumps(manifest, ensure_ascii=False))
        self.env = os.environ.copy()
        for key in ("BCN_BOT_TOKEN", "BOT_DATA_DIR", "OPENCLAW_DATA_DIR", "BCSFUSE_FORCE_ENABLE_FUSION"):
            self.env.pop(key, None)
        self.env.pop("BCN_BENCH_DEFAULT_JUDGE_KEY", None)
        self.env.update(STANDALONE_OPENCLAW_ROOT=str(self.root))
        if config["model_mode"] == "reuse":
            model_file = Path(os.environ.get("SINGLEBOX_MODEL_CONFIG_FILE", str(config["repository"] / "singlebox/.dependencies/openclaw/model-config/openclaw.json"))).expanduser().resolve()
            self.env.update(SINGLEBOX_MODEL_CONFIG_PREPARED="1",
                            SINGLEBOX_MODEL_CONFIG_FILE=str(model_file),
                            OPENCLAW_MODEL_CONFIG_SOURCE=str(model_file))
        else:
            self.env.update(SINGLEBOX_MODEL_CONFIG_MODE=config["model_mode"],
                            SINGLEBOX_MODEL_CONFIG_HOME_CONFIRMED="1")

    def command(self, action):
        # Singlebox may log private runtime configuration. Retain locally only (run dir mode 0700).
        with (self.run_dir / f"singlebox-{action}.log").open("w") as log:
            proc = subprocess.Popen(["bash", str(self.config["repository"] / "singlebox/singlebox.sh"),
                action, "bots", "--profile-dir", str(self.profile)], cwd=self.config["repository"],
                env=self.env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=self.config["startup_timeout_seconds"])
            except BaseException:
                os.killpg(proc.pid, signal.SIGTERM)
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                raise
            if code:
                raise RuntimeError(f"Singlebox {action} failed; inspect private local log")

    def verify_stopped(self):
        # A successful stop script is not proof that all listeners exited.
        for port in self.ports:
            with socket.socket() as probe:
                probe.settimeout(.5)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError("Owned Bot port remains active; inspect run metadata")

    def members(self):
        result = {}
        for alias, name in self.mapping.items():
            profile = self.root / "profiles" / name
            config = json.loads((profile / "openclaw.json").read_text())
            workspace = Path(config.get("agents", {}).get("defaults", {}).get("workspace", "")).resolve()
            if not workspace.is_relative_to(self.root.resolve()):
                raise ValueError("Singlebox did not honor isolated workspace; stop and review")
            session = json.loads((profile / ".bcs/session.json").read_text())
            result[alias] = session["bot_uuid"]
        if len(set(result.values())) != 5:
            raise ValueError("Duplicate Bot identity")
        return result

    def bcn_env(self):
        env = self.env.copy()
        env["BOT_DATA_DIR"] = str(self.root / "profiles" / self.mapping["ceo"])
        return env
