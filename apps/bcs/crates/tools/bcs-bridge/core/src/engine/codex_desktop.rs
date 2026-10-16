//! Attach to an existing Codex app-server through its Unix WebSocket listener.
use std::path::PathBuf;
use std::sync::Arc;

use super::{Engine, EngineFactory, codex_app_server::CodexAppServer};

pub struct CodexDesktopFactory { codex_home: Option<PathBuf> }

impl Default for CodexDesktopFactory {
    fn default() -> Self {
        Self { codex_home: dirs::home_dir().map(|home| home.join(".codex")) }
    }
}

impl CodexDesktopFactory {
    /// Codex home is supplied by the application configuration/composition root.
    pub fn with_codex_home(home: PathBuf) -> Self { Self { codex_home: Some(home) } }
}

impl EngineFactory for CodexDesktopFactory {
    fn id(&self) -> &str { "codex-desktop" }
    fn default_bin(&self) -> &str { "" }
    fn requires_bin(&self) -> bool { false }
    fn registration_requires_options(&self) -> bool { true }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        if options.keys().any(|key| key != "socket") {
            return Err("codex-desktop supports only the socket engine option".into());
        }
        let socket = match options.get("socket") {
            Some(value) => {
                let value = value.as_str().filter(|value| !value.trim().is_empty())
                    .ok_or("codex-desktop socket must be a nonempty absolute path")?;
                let path = PathBuf::from(value);
                if !path.is_absolute() { return Err("codex-desktop socket must be an absolute path".into()); }
                path
            }
            None => self.codex_home.as_ref().filter(|home| home.is_absolute())
                .ok_or("Cannot locate an absolute Codex home for desktop detection")?
                .join("app-server-control/app-server-control.sock"),
        };
        if !bin.as_os_str().is_empty() { return Err("codex-desktop does not launch an executable".into()); }
        Ok(Arc::new(CodexAppServer::desktop(socket)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_accepts_only_an_absolute_socket_option() {
        let factory = CodexDesktopFactory::with_codex_home("/tmp/codex-home".into());
        assert!(factory.build(PathBuf::new(), &toml::Table::new()).is_ok());
        for value in ["", "relative.sock"] {
            let options = toml::Table::from_iter([("socket".into(), value.into())]);
            assert!(factory.build(PathBuf::new(), &options).is_err());
        }
        let options = toml::Table::from_iter([("socket".into(), "/tmp/desktop.sock".into())]);
        assert!(factory.build(PathBuf::new(), &options).is_ok());
        let options = toml::Table::from_iter([("socket".into(), true.into())]);
        assert!(factory.build(PathBuf::new(), &options).is_err());
        let options = toml::Table::from_iter([("unknown".into(), true.into())]);
        assert!(factory.build(PathBuf::new(), &options).is_err());
    }
}
