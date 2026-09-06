"""
Capability declaration system.

Every engine declares an EngineCapabilities at construction time. The web layer
and frontend use this to decide whether to dispatch a request, surface a warning,
or hide a feature entirely.

See src/engine/docs/heterogeneous-engine-architecture.md §3.2 and §4.1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(Enum):
    """Vocabulary of capabilities an engine may declare.

    Capability strings are the stable contract between backend and frontend. Adding
    new entries is safe; renaming or removing entries is a breaking change.
    """

    # ── Session ──
    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_DELETE = "session.delete"
    SESSION_UPDATE = "session.update"
    SESSION_HISTORY = "session.history"
    SESSION_ARCHIVE = "session.archive"

    # ── Chat ──
    CHAT_STREAM = "chat.stream"
    CHAT_COMPLETE = "chat.complete"
    CHAT_ABORT = "chat.abort"
    CHAT_APPROVAL = "chat.approval"
    CHAT_HISTORY = "chat.history"
    # ``CHAT_INTERACTION`` covers AskUserQuestion-style mid-run clarification
    # requests that the engine surfaces back to the user (aicoding §7). The
    # client resolves it via an engine-specific RPC (``interaction.resolve``
    # on aicoding). Declaring the capability tells the frontend it can show
    # an inline question prompt.
    CHAT_INTERACTION = "chat.interaction"
    # ``CHAT_MODE_TRANSITION`` covers ExitPlanMode-style in-run mode changes
    # (aicoding §8): the engine asks permission to move from plan→act,
    # read→write, etc. Resolved via ``mode_transition.resolve`` on aicoding.
    CHAT_MODE_TRANSITION = "chat.mode_transition"

    # ── MCP ──
    MCP_LIST = "mcp.list"
    MCP_CREATE = "mcp.create"
    MCP_UPDATE = "mcp.update"
    MCP_DELETE = "mcp.delete"
    MCP_START = "mcp.start"
    MCP_STOP = "mcp.stop"
    MCP_TOOLS_LIST = "mcp.tools.list"
    MCP_TOOLS_CALL = "mcp.tools.call"
    MCP_RESOURCES_LIST = "mcp.resources.list"
    MCP_RESOURCES_READ = "mcp.resources.read"
    MCP_PROMPTS_LIST = "mcp.prompts.list"
    MCP_PROMPTS_GET = "mcp.prompts.get"
    MCP_FILTER_SERVERS = "mcp.filter_servers"

    # ── Skills ──
    SKILLS_LIST = "skills.list"
    SKILLS_INSTALL = "skills.install"
    SKILLS_UNINSTALL = "skills.uninstall"
    SKILLS_UPDATE = "skills.update"
    SKILLS_EXECUTE = "skills.execute"
    SKILLS_DISCOVER = "skills.discover"
    SKILLS_SYNC_SYMLINKS = "skills.sync_symlinks"
    SKILLS_SYNC_BINDPATHS = "skills.sync_bindpaths"
    SKILLS_CLEAN_SYMLINKS = "skills.clean_symlinks"
    SKILLS_CENTER_ENSURE = "skills.center_ensure"

    # ── CLI tools ──
    # Model-callable command-line binaries placed into the bot by a config
    # manifest (W9). See docs/bot-config-manifest/engine-requirements.zh-CN.md
    # §4 A2 for the endpoint contract these gate.
    CLI_INSTALL = "cli.install"
    CLI_DELETE = "cli.delete"
    CLI_LIST = "cli.list"
    CLI_REPLACE = "cli.replace"
    CLI_DOWNLOAD = "cli.download"

    # ── Default config ──
    DEFAULT_CONFIG_GET = "default_config.get"

    # ── Web shell ──
    WEB_SHELL_OPEN = "web_shell.open"

    # ── Approval ──
    APPROVAL_GET = "approval.get"
    APPROVAL_SET = "approval.set"
    APPROVAL_LIST = "approval.list"

    # ── File ──
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    FILE_UPLOAD = "file.upload"
    FILE_DELETE = "file.delete"
    FILE_LIST = "file.list"
    FILE_MKDIR = "file.mkdir"

    # ── Bash ──
    BASH_EXEC = "bash.exec"

    # ── Node ──
    NODE_LIST = "node.list"
    NODE_REGISTER = "node.register"
    NODE_UNREGISTER = "node.unregister"
    NODE_STATUS = "node.status"

    # ── Channel ──
    CHANNEL_CONFIG_GET = "channel.config.get"
    CHANNEL_CONFIG_SET = "channel.config.set"
    CHANNEL_STATUS = "channel.status"

    # ── Cron ──
    CRON_LIST = "cron.list"
    CRON_CREATE = "cron.create"
    CRON_UPDATE = "cron.update"
    CRON_DELETE = "cron.delete"
    CRON_RUN = "cron.run"
    CRON_HISTORY = "cron.history"

    # ── Model ──
    MODEL_LIST = "model.list"
    MODEL_SWITCH = "model.switch"

    # ── Health ──
    HEALTH_CHECK = "health.check"
    HEALTH_COMPONENTS = "health.components"
    HEALTH_METRICS = "health.metrics"

    # ── Effect ──
    EFFECT_TRACK = "effect.track"
    EFFECT_EVALUATE = "effect.evaluate"
    EFFECT_FEEDBACK = "effect.feedback"
    EFFECT_REPORT = "effect.report"

    # ── Auto Initiate ──
    AUTO_INITIATE_EXECUTE = "auto_initiate.execute"

    # ── Data proxy ──
    # ``/data/*`` reverse proxy on the engine adapter. Each engine that
    # serves the capability owns its upstream choice (aicoding forwards
    # to harness-data); engines that don't declare it cause the route
    # to 501 via check_capability.
    DATA_PROXY_FORWARD = "data_proxy.forward"


@dataclass
class EngineCapabilities:
    """An engine's capability declaration.

    All three fields use Capability as the key. Values in `limited` and `fallback`
    are free-form human-readable strings — the platform does not interpret them,
    it just relays them to the caller (frontend, HTTP response body).

    `supported`: capabilities that work as documented. The route dispatches normally.

    `limited`: capabilities that work but with a caveat. The value is a description
        of the limitation (e.g. "只能列出当前会话" — "can only list the current
        session"). The route still dispatches; the response includes the message
        as a warning so the UI can surface it.

    `fallback`: capabilities that the engine cannot service directly, but for
        which an alternate path exists outside the standard API. The value is a
        message telling the user how to accomplish the task another way (e.g.
        "通过编辑 ~/.claude/mcp.json 配置文件" — "by editing the
        ~/.claude/mcp.json config file"). The route returns HTTP 501 with this
        message in the body.
    """

    supported: set[Capability] = field(default_factory=set)
    limited: dict[Capability, str] = field(default_factory=dict)
    fallback: dict[Capability, str] = field(default_factory=dict)

    def supports(self, cap: Capability) -> bool:
        """Return True if `cap` is fully or partially supported."""
        return cap in self.supported or cap in self.limited

    def is_limited(self, cap: Capability) -> bool:
        """Return True if `cap` is supported but with a caveat."""
        return cap in self.limited

    def get_limitation(self, cap: Capability) -> str | None:
        """Return the limitation description for `cap`, or None."""
        return self.limited.get(cap)

    def has_fallback(self, cap: Capability) -> bool:
        """Return True if `cap` has a fallback path declared."""
        return cap in self.fallback

    def get_fallback(self, cap: Capability) -> str | None:
        """Return the fallback description for `cap`, or None."""
        return self.fallback.get(cap)

    def to_dict(self) -> dict:
        """Serialize for transport to the frontend."""
        return {
            "supported": sorted(c.value for c in self.supported),
            "limited": {k.value: v for k, v in self.limited.items()},
            "fallback": {k.value: v for k, v in self.fallback.items()},
        }


__all__ = ["Capability", "EngineCapabilities"]
