use std::{collections::HashSet, net::SocketAddr, path::{Path, PathBuf}};
use serde::Deserialize;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ConnectionMode { #[default] Gateway, Plugin }

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginConfig {
    pub url: String,
    #[serde(default = "default_reconnect_interval_ms")]
    pub reconnect_interval_ms: u64,
    #[serde(default = "default_heartbeat_interval_ms")]
    pub heartbeat_interval_ms: u64,
    #[serde(default = "default_connect_timeout_ms")]
    pub connect_timeout_ms: u64,
}

fn default_reconnect_interval_ms() -> u64 { 1_000 }
fn default_heartbeat_interval_ms() -> u64 { 30_000 }
fn default_connect_timeout_ms() -> u64 { 10_000 }

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BotConfig {
    pub provider_bot_ref: String,
    pub bot_id: Option<String>,
    pub token: Option<String>,
    /// Engine id resolved through the [`crate::engine::EngineRegistry`].
    pub engine: String,
    pub model: Option<String>,
    pub cwd: PathBuf,
    pub permission_mode: Option<String>,
    /// Engine executable; the engine factory's default binary name when absent.
    pub engine_bin: Option<PathBuf>,
    /// Engine-specific settings, validated by the engine factory.
    #[serde(default)]
    pub engine_options: toml::Table,
}

impl std::fmt::Debug for BotConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BotConfig")
            .field("provider_bot_ref", &self.provider_bot_ref)
            .field("bot_id", &self.bot_id)
            .field("token", &self.token.as_ref().map(|_| "[redacted]"))
            .field("engine", &self.engine)
            .field("model", &self.model)
            .field("cwd", &self.cwd)
            .field("permission_mode", &self.permission_mode)
            .field("engine_bin", &self.engine_bin)
            .field("engine_options", &self.engine_options)
            .finish()
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderConfig {
    pub provider_id: String,
    #[serde(default)]
    pub mode: ConnectionMode,
    pub listen: Option<SocketAddr>,
    pub bcs_to_provider_token: Option<String>,
    pub plugin: Option<PluginConfig>,
    pub bot_runtime_token: Option<String>,
    #[serde(default)]
    pub trace_dir: Option<PathBuf>,
    #[serde(default = "default_state_path")]
    pub state_path: PathBuf,
    #[serde(rename = "bot")]
    pub bots: Vec<BotConfig>,
}

fn default_state_path() -> PathBuf {
    PathBuf::from("~/.bcn-bridge/bridge-state.sqlite3")
}

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("cannot locate the user home directory; configure state_path explicitly")]
    HomeUnavailable,
    #[error("read config: {0}")]
    Read(#[from] std::io::Error),
    #[error("parse config: {0}")]
    Parse(#[from] toml::de::Error),
    #[error("invalid config: {0}")]
    Invalid(&'static str),
}

impl ProviderConfig {
    pub fn load(path: &Path) -> Result<Self, ConfigError> {
        let text = std::fs::read_to_string(path)?;
        Self::from_toml(&text, path)
    }

    /// Parse a runtime document with file-relative paths resolved at its source.
    pub fn from_toml(text: &str, path: &Path) -> Result<Self, ConfigError> {
        let mut config: Self = toml::from_str(text)?;
        config.validate()?;
        if let Ok(suffix) = config.state_path.strip_prefix("~") {
            let home = dirs::home_dir().filter(|home| home.is_absolute()).ok_or(ConfigError::HomeUnavailable)?;
            config.state_path = home.join(suffix);
        } else if config.state_path.is_relative() {
            config.state_path = path.parent().unwrap_or_else(|| Path::new(".")).join(&config.state_path);
        }
        Ok(config)
    }

    fn validate(&self) -> Result<(), ConfigError> {
        if self.provider_id.trim().is_empty() {
            return Err(ConfigError::Invalid("provider_id must be nonempty"));
        }
        let mut refs = HashSet::new();
        for bot in &self.bots {
            if bot.provider_bot_ref.trim().is_empty() || !refs.insert(&bot.provider_bot_ref) {
                return Err(ConfigError::Invalid("provider_bot_ref must be nonempty and unique"));
            }
            if bot.engine.trim().is_empty() {
                return Err(ConfigError::Invalid("bot.engine must be nonempty"));
            }
        }
        match self.mode {
            ConnectionMode::Gateway => {
                if self.listen.is_none() {
                    return Err(ConfigError::Invalid("gateway requires listen"));
                }
                if self.bcs_to_provider_token.as_deref().is_none_or(|token| token.trim().is_empty()) {
                    return Err(ConfigError::Invalid("gateway requires a nonempty bcs_to_provider_token"));
                }
            }
            ConnectionMode::Plugin => {
                let plugin = self.plugin.as_ref().ok_or(ConfigError::Invalid("plugin mode requires plugin configuration"))?;
                let url = url::Url::parse(&plugin.url).map_err(|_| ConfigError::Invalid("plugin.url must be an absolute ws/wss URL"))?;
                if !matches!(url.scheme(), "ws" | "wss") || url.host_str().is_none() {
                    return Err(ConfigError::Invalid("plugin.url must be an absolute ws/wss URL"));
                }
                if !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
                    return Err(ConfigError::Invalid("plugin.url must not contain userinfo, query parameters or fragments; configure bot.token separately"));
                }
                if plugin.reconnect_interval_ms == 0 || plugin.heartbeat_interval_ms == 0 || plugin.connect_timeout_ms == 0 {
                    return Err(ConfigError::Invalid("plugin intervals and timeouts must be positive"));
                }
                let mut ids = HashSet::new();
                for bot in &self.bots {
                    let id = bot.bot_id.as_deref().filter(|id| !id.trim().is_empty())
                        .ok_or(ConfigError::Invalid("plugin bots require a nonempty bot_id"))?;
                    if !ids.insert(id) {
                        return Err(ConfigError::Invalid("plugin bot_id values must be unique"));
                    }
                    if bot.token.as_deref().is_some_and(|token| token.trim().is_empty()) {
                        return Err(ConfigError::Invalid("bot.token must be nonempty when configured"));
                    }
                }
            }
        }
        Ok(())
    }
    pub fn bot(&self, provider_bot_ref: &str) -> Option<&BotConfig> {
        self.bots.iter().find(|b| b.provider_bot_ref == provider_bot_ref)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLUGIN: &str = r#"
mode = "plugin"
provider_id = "bridge-1"
state_path = "bridge-state.sqlite3"
[plugin]
url = "ws://127.0.0.1:21000/ws/bot"
[[bot]]
provider_bot_ref = "worker-a"
bot_id = "bot-a"
engine = "claude-code"
cwd = "/tmp"
"#;

    fn load_document(document: &str) -> Result<ProviderConfig, ConfigError> {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        std::fs::write(&path, document).unwrap();
        ProviderConfig::load(&path)
    }

    #[test]
    fn plugin_loads_without_gateway_listener_or_token() {
        let result = load_document(PLUGIN);
        assert!(result.is_ok(), "plugin config rejected: {result:?}");
        let cfg = result.unwrap();
        assert_eq!(cfg.mode, ConnectionMode::Plugin);
        assert!(cfg.listen.is_none());
        assert!(cfg.bcs_to_provider_token.is_none());
        let plugin = cfg.plugin.unwrap();
        assert_eq!(plugin.url, "ws://127.0.0.1:21000/ws/bot");
        assert!(plugin.reconnect_interval_ms > 0);
        assert!(plugin.heartbeat_interval_ms > 0);
        assert!(plugin.connect_timeout_ms > 0);
    }

    #[test]
    fn plugin_supports_wss_and_explicit_bot_credentials_and_timers() {
        let document = PLUGIN.replace("ws://", "wss://")
            .replace("[plugin]", "[plugin]\nreconnect_interval_ms = 321\nheartbeat_interval_ms = 456\nconnect_timeout_ms = 789")
            .replace("bot_id = \"bot-a\"", "bot_id = \"bot-a\"\ntoken = \"test-bot-token\"");
        let cfg = load_document(&document).unwrap();
        let bot = cfg.bot("worker-a").unwrap();
        assert_eq!(bot.bot_id.as_deref(), Some("bot-a"));
        assert_eq!(bot.token.as_deref(), Some("test-bot-token"));
        let plugin = cfg.plugin.unwrap();
        assert_eq!(plugin.reconnect_interval_ms, 321);
        assert_eq!(plugin.heartbeat_interval_ms, 456);
        assert_eq!(plugin.connect_timeout_ms, 789);
    }

    #[test]
    fn config_diagnostics_do_not_expose_credentials() {
        let document = PLUGIN.replace("bot_id = \"bot-a\"", "bot_id = \"bot-a\"\ntoken = \"private-bot-credential\"");
        let cfg = load_document(&document).unwrap();
        assert!(!format!("{cfg:?}").contains("private-bot-credential"));
        let invalid = document.replace("ws://127.0.0.1:21000/ws/bot", "ws://user:private-url-credential@127.0.0.1/ws/bot?token=private-query-credential");
        let error = load_document(&invalid).unwrap_err();
        for diagnostic in [error.to_string(), format!("{error:?}")] {
            assert!(!diagnostic.contains("private-url-credential"));
            assert!(!diagnostic.contains("private-query-credential"));
        }
    }

    #[test]
    fn gateway_requires_listener_and_nonempty_token() {
        let gateway = "provider_id = 'bridge-1'\nlisten = '127.0.0.1:0'\nbcs_to_provider_token = 'token'\nbot = []\n";
        for invalid in [
            gateway.replace("listen = '127.0.0.1:0'\n", ""),
            gateway.replace("bcs_to_provider_token = 'token'\n", ""),
            gateway.replace("'token'", "'   '"),
        ] {
            assert!(load_document(&invalid).is_err(), "invalid gateway config was accepted");
        }
    }

    #[test]
    fn config_rejects_unknown_fields_at_every_level() {
        let gateway = "provider_id = 'bridge-1'\nlisten = '127.0.0.1:0'\nbcs_to_provider_token = 'token'\nbot = []\n";
        for invalid in [
            format!("{gateway}lissten = '127.0.0.1:1'\n"),
            PLUGIN.replace("[plugin]", "[plugin]\nreconnect_intervall_ms = 100"),
            PLUGIN.replace("[[bot]]", "[[bot]]\nbo_id = 'mistyped'"),
            PLUGIN.replace("mode = \"plugin\"", "mode = \"plugim\""),
        ] {
            assert!(load_document(&invalid).is_err(), "unknown config field was accepted");
        }
    }

    #[test]
    fn plugin_rejects_invalid_urls_credentials_and_timers() {
        for url in ["", "http://127.0.0.1/ws/bot", "ws://", "relative/path",
            "ws://user:secret@127.0.0.1/ws/bot", "ws://127.0.0.1/ws/bot?token=secret",
            "ws://127.0.0.1/ws/bot#fragment"] {
            let invalid = PLUGIN.replace("ws://127.0.0.1:21000/ws/bot", url);
            assert!(load_document(&invalid).is_err(), "invalid plugin URL was accepted");
        }
        for field in ["reconnect_interval_ms", "heartbeat_interval_ms", "connect_timeout_ms"] {
            let invalid = PLUGIN.replace("[plugin]", &format!("[plugin]\n{field} = 0"));
            assert!(load_document(&invalid).is_err(), "zero {field} was accepted");
        }
    }

    #[test]
    fn plugin_requires_namespace_endpoint_and_unique_bot_bindings() {
        let bot = "\n[[bot]]\nprovider_bot_ref = 'worker-b'\nbot_id = 'bot-a'\nengine = 'claude-code'\ncwd = '/tmp'\n";
        for invalid in [
            PLUGIN.replace("provider_id = \"bridge-1\"", "provider_id = \" \""),
            PLUGIN.replace("[plugin]\nurl = \"ws://127.0.0.1:21000/ws/bot\"\n", ""),
            PLUGIN.replace("provider_bot_ref = \"worker-a\"", "provider_bot_ref = \" \""),
            PLUGIN.replace("bot_id = \"bot-a\"\n", ""),
            PLUGIN.replace("bot_id = \"bot-a\"", "bot_id = \" \""),
            PLUGIN.replace("bot_id = \"bot-a\"", "bot_id = \"bot-a\"\ntoken = \" \""),
            format!("{PLUGIN}{bot}"),
            format!("{PLUGIN}{}", bot.replace("worker-b", "worker-a").replace("bot-a", "bot-b")),
        ] {
            assert!(load_document(&invalid).is_err(), "invalid plugin binding was accepted");
        }
    }

    #[test]
    fn loads_provider_config_and_finds_bot() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        std::fs::write(
            &path,
            r#"
provider_id = "bridge-1"
listen = "127.0.0.1:21100"
bcs_to_provider_token = "tok-b2p"
state_path = "bridge-state.sqlite3"

[[bot]]
provider_bot_ref = "cc-worker"
engine = "claude-code"
model = "sonnet"
cwd = "/tmp"
"#,
        )
        .unwrap();
        let cfg = ProviderConfig::load(&path).unwrap();
        assert_eq!(cfg.provider_id, "bridge-1");
        assert_eq!(cfg.mode, ConnectionMode::Gateway);
        assert_eq!(cfg.listen, Some("127.0.0.1:21100".parse().unwrap()));
        assert_eq!(cfg.bcs_to_provider_token.as_deref(), Some("tok-b2p"));
        assert!(cfg.plugin.is_none());
        assert_eq!(cfg.state_path, dir.path().join("bridge-state.sqlite3"));
        let bot = cfg.bot("cc-worker").unwrap();
        assert_eq!(bot.engine, "claude-code");
        assert!(bot.engine_options.is_empty());
        assert_eq!(bot.model.as_deref(), Some("sonnet"));
        assert!(cfg.bot("nope").is_none());
    }

    #[test]
    fn rejects_blank_engine_and_accepts_engine_options() {
        let blank = PLUGIN.replace("engine = \"claude-code\"", "engine = \" \"");
        assert!(load_document(&blank).is_err(), "blank engine was accepted");
        let options = PLUGIN.replace("cwd = \"/tmp\"", "cwd = \"/tmp\"\nengine_options = { sandbox = \"read-only\" }");
        let cfg = load_document(&options).unwrap();
        assert_eq!(cfg.bots[0].engine_options["sandbox"].as_str(), Some("read-only"));
    }

    #[test]
    fn rejects_non_string_engine() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        std::fs::write(
            &path,
            r#"
provider_id = "bridge-1"
listen = "127.0.0.1:21100"
bcs_to_provider_token = "t"
[[bot]]
provider_bot_ref = "x"
engine = 42
cwd = "/tmp"
"#,
        )
        .unwrap();
        assert!(ProviderConfig::load(&path).is_err());
    }
}
