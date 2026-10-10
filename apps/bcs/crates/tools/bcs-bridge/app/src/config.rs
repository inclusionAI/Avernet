use std::{fs::{self, File, OpenOptions}, io::Write, path::{Path, PathBuf}};

use anyhow::{Context, bail};
use bcs_bridge_core::config::ProviderConfig;
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;

use crate::credentials::CredentialSource;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RegistrationSettings {
    /// Registration API prefix; `--api-url` or the deploy profile default when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_url: Option<String>,
}
impl Default for RegistrationSettings {
    fn default() -> Self { Self { api_url: None } }
}

fn parse(text: &str) -> anyhow::Result<(RegistrationSettings, toml::Table)> {
    let mut table: toml::Table = toml::from_str(text).map_err(|_| anyhow::anyhow!("Invalid bridge TOML configuration"))?;
    let settings = match table.remove("registration") {
        Some(value) => value.try_into().map_err(|_| anyhow::anyhow!("Invalid [registration] configuration; expected api_url"))?,
        None => RegistrationSettings::default(),
    };
    Ok((settings, table))
}

pub fn load_runtime(path: &Path, credentials: &dyn CredentialSource) -> anyhow::Result<ProviderConfig> {
    let text = fs::read_to_string(path).context("Cannot read bridge configuration")?;
    let (_, table) = parse(&text)?;
    let webhook_token = if table.get("mode").and_then(toml::Value::as_str) == Some("plugin") {
        None
    } else {
        let provider = table.get("provider_id").and_then(toml::Value::as_str)
            .context("Gateway configuration requires provider_id")?;
        Some(credentials.webhook_token(provider)?)
    };
    runtime(&table, path, webhook_token.as_deref())
}

/// Supply the resolved webhook credential in memory; never persist the runtime copy.
pub fn runtime(table: &toml::Table, path: &Path, webhook_token: Option<&str>) -> anyhow::Result<ProviderConfig> {
    let mut runtime = table.clone();
    // Read old configuration files, but never trust their credential as a fallback.
    runtime.remove("bcs_to_provider_token");
    if let Some(token) = webhook_token {
        runtime.insert("bcs_to_provider_token".into(), token.into());
    }
    let text = toml::to_string(&runtime).context("Cannot encode runtime configuration")?;
    ProviderConfig::from_toml(&text, path).map_err(|_| anyhow::anyhow!("Invalid bridge runtime configuration"))
}

pub struct Bootstrap {
    pub settings: RegistrationSettings,
    pub original: Option<String>,
}

pub fn read_bootstrap(path: &Path) -> anyhow::Result<Bootstrap> {
    let original = read_existing(path)?;
    let (settings, mut table) = parse(original.as_deref().unwrap_or(""))?;
    if table.contains_key("provider_id") || table.contains_key("bot") {
        bail!("A bridge runtime configuration already exists; refusing to overwrite it");
    }
    table.remove("bcs_to_provider_token");
    if !table.is_empty() { bail!("Before registration, configuration may contain only [registration] settings"); }
    Ok(Bootstrap { settings, original })
}

fn read_existing(path: &Path) -> anyhow::Result<Option<String>> {
    match fs::symlink_metadata(path) {
        Ok(meta) if !meta.file_type().is_file() => bail!("Bridge configuration must be a regular file, not a symlink or directory"),
        Ok(_) => fs::read_to_string(path).map(Some).context("Cannot read bridge configuration"),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => bail!("Cannot inspect bridge configuration"),
    }
}

struct RegistrationLock { path: PathBuf, _file: File }
impl Drop for RegistrationLock { fn drop(&mut self) { let _ = fs::remove_file(&self.path); } }

pub struct PreparedConfig {
    path: PathBuf,
    original: Option<String>,
    _lock: RegistrationLock,
    temporary: NamedTempFile,
}

impl PreparedConfig {
    pub fn new(path: &Path, original: Option<String>) -> anyhow::Result<Self> {
        let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true);
        #[cfg(unix)] {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(parent).context("Cannot create bridge configuration directory")?;
        let mut lock_path = path.as_os_str().to_owned();
        lock_path.push(".register.lock");
        let lock_path = PathBuf::from(lock_path);
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)] {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&lock_path).context("Cannot reserve configuration; another registration may be running (check .register.lock)")?;
        let lock = RegistrationLock { path: lock_path, _file: file };
        if read_existing(path)? != original { bail!("Configuration changed before registration; retry after reviewing it"); }
        let temporary = tempfile::Builder::new().prefix(".bridge-registration-").suffix(".toml")
            .tempfile_in(parent).context("Cannot prepare a private bridge configuration file")?;
        Ok(Self { path: path.into(), original, _lock: lock, temporary })
    }

    pub fn save(mut self, text: &str) -> anyhow::Result<()> {
        if self.temporary.write_all(text.as_bytes()).is_err() {
            return recovery(self.temporary, "writing configuration failed; recovery file may be incomplete");
        }
        if self.temporary.as_file().sync_all().is_err() {
            return recovery(self.temporary, "syncing configuration failed; recovery file durability is not guaranteed");
        }
        if read_existing(&self.path).ok().as_ref() != Some(&self.original) {
            return recovery(self.temporary, "configuration changed while registering");
        }
        let installed = if self.original.is_some() {
            self.temporary.persist(&self.path)
        } else {
            self.temporary.persist_noclobber(&self.path)
        };
        match installed {
            Ok(_) => Ok(()),
            Err(error) => recovery(error.file, "cannot install configuration"),
        }
    }
}

fn recovery(file: NamedTempFile, reason: &str) -> anyhow::Result<()> {
    match file.keep() {
        Ok((_, path)) => bail!("Bot registered, but {reason}; private recovery configuration saved at {}. Review it before using --config to start", path.display()),
        Err(_) => bail!("Bot registered, but {reason} and recovery configuration could not be retained"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registration_defaults_need_no_bootstrap_file_or_webhook_credential() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        let bootstrap = read_bootstrap(&path).unwrap();
        // api_url is resolved from --api-url or the deploy profile, not defaulted in the file.
        assert_eq!(bootstrap.settings.api_url, None);
        assert!(bootstrap.original.is_none());
        assert!(!path.exists());
        fs::write(&path, "bcs_to_provider_token = 'legacy-token'\n").unwrap();
        assert_eq!(read_bootstrap(&path).unwrap().settings.api_url, None);
    }

    #[test]
    fn a_new_config_is_private_and_the_registration_lock_is_released() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested/bridge.toml");
        let prepared = PreparedConfig::new(&path, None).unwrap();
        assert!(!path.exists());
        assert!(PreparedConfig::new(&path, None).is_err());
        prepared.save("private complete configuration").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "private complete configuration");
        assert!(!path.with_file_name("bridge.toml.register.lock").exists());
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
            assert_eq!(fs::metadata(path.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
        }
    }

    #[test]
    fn concurrent_edits_preserve_the_original_and_retain_private_recovery_credentials() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        fs::write(&path, "bootstrap").unwrap();
        let prepared = PreparedConfig::new(&path, Some("bootstrap".into())).unwrap();
        fs::write(&path, "edited while registering").unwrap();
        let error = prepared.save("token = 'new-private-token'").unwrap_err().to_string();
        assert!(error.contains("private recovery configuration saved"));
        assert!(!error.contains("new-private-token"));
        assert_eq!(fs::read_to_string(&path).unwrap(), "edited while registering");
        let recovery = fs::read_dir(dir.path()).unwrap().map(|entry| entry.unwrap().path())
            .find(|path| path.file_name().unwrap().to_string_lossy().starts_with(".bridge-registration-")).unwrap();
        assert_eq!(fs::read_to_string(&recovery).unwrap(), "token = 'new-private-token'");
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&recovery).unwrap().permissions().mode() & 0o777, 0o600);
        }
        assert!(!path.with_file_name("bridge.toml.register.lock").exists());
    }

    #[test]
    fn write_failure_preserves_the_recovery_file_and_warns_it_may_be_incomplete() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bridge.toml");
        let mut prepared = PreparedConfig::new(&path, None).unwrap();
        // Make the real file descriptor unwritable after preflight, like an I/O failure after POST.
        let readonly = File::open(prepared.temporary.path()).unwrap();
        let (_, temporary_path) = prepared.temporary.into_parts();
        let recovery_path = temporary_path.to_path_buf();
        prepared.temporary = NamedTempFile::from_parts(readonly, temporary_path);
        let error = prepared.save("token = 'private-new-token'").unwrap_err().to_string();
        assert!(error.contains("may be incomplete"), "{error}");
        assert!(recovery_path.exists());
        assert!(!error.contains("private-new-token"));
        assert!(!path.exists());
    }
}
