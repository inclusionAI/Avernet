//! Registration-time workspace selection; never migrates existing Bot state.
use std::{fs, path::{Path, PathBuf}};

use anyhow::{Context, bail};

pub(super) enum Workspace {
    Resolved(PathBuf),
    Managed(PathBuf),
}

impl Workspace {
    pub(super) fn prepare(explicit: Option<&Path>, subbots: bool) -> anyhow::Result<Self> {
        if let Some(path) = explicit {
            return Ok(Self::Resolved(canonical_directory(path)?));
        }
        let home = dirs::home_dir().context("Cannot locate home directory; specify --cwd")?;
        if subbots { return Self::managed(&home.join(".bcn-bridge/bots")); }
        let path = home.join("workspace");
        fs::create_dir_all(&path).context("Cannot create default engine working directory")?;
        Ok(Self::Resolved(canonical_directory(&path)?))
    }

    fn managed(root: &Path) -> anyhow::Result<Self> {
        private_directory(root, true).context("Cannot create subbot workspace root")?;
        let root = canonical_directory(root)?;
        // Check writability before contacting registration. No Bot UUID exists yet.
        tempfile::tempfile_in(&root).context("Subbot workspace root is not writable")?;
        Ok(Self::Managed(root))
    }

    /// Identity hooks run before the UUID is assigned; no engine runs in this path.
    pub(super) fn identity_cwd(&self) -> &Path {
        match self { Self::Resolved(path) | Self::Managed(path) => path }
    }

    pub(super) fn assigned_cwd(&self, bot_id: &str) -> PathBuf {
        match self {
            Self::Resolved(path) => path.clone(),
            // RegistrationClient validates Bot IDs before they can become path components.
            Self::Managed(root) => root.join(bot_id),
        }
    }

    pub(super) fn check_explicit(&self, document: &toml::Table, config: &Path) -> anyhow::Result<()> {
        if let Self::Resolved(path) = self { ensure_exclusive(path, document, config)?; }
        Ok(())
    }

    pub(super) fn create_assigned(&self, cwd: &Path, document: &toml::Table, config: &Path) -> anyhow::Result<()> {
        if matches!(self, Self::Managed(_)) {
            // Atomic exclusive creation: never reuse a pre-existing directory, file or symlink.
            private_directory(cwd, false).context("Cannot exclusively create the registered Bot workspace")?;
        }
        let canonical = canonical_directory(cwd)?;
        ensure_exclusive(&canonical, document, config)
    }
}

fn private_directory(path: &Path, recursive: bool) -> std::io::Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.recursive(recursive);
    #[cfg(unix)] {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(path)
}

fn canonical_directory(path: &Path) -> anyhow::Result<PathBuf> {
    let path = path.canonicalize().context("Cannot resolve engine working directory")?;
    if !path.is_dir() { bail!("Engine working directory must be a directory"); }
    if path.to_str().is_none() { bail!("Engine working directory must be UTF-8"); }
    Ok(path)
}

fn ensure_exclusive(cwd: &Path, document: &toml::Table, config: &Path) -> anyhow::Result<()> {
    let Some(bots) = document.get("bot").and_then(toml::Value::as_array) else { return Ok(()); };
    for bot in bots {
        let path = bot.get("cwd").and_then(toml::Value::as_str).context("Existing Bot has no working directory")?;
        let path = Path::new(path);
        let path = if path.is_absolute() { path.to_path_buf() }
            else { config.parent().unwrap_or(Path::new(".")).join(path) };
        if canonical_directory(&path)? == cwd {
            bail!("Subbots must use exclusive working directories; another Bot in this configuration already uses --cwd");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_workspaces_use_the_full_bot_id_and_never_reuse_existing_paths() {
        let home = tempfile::tempdir().unwrap();
        let workspace = Workspace::managed(&home.path().join("bots")).unwrap();
        let config = home.path().join("bridge.toml");
        let id = format!("bot_{}", uuid::Uuid::new_v4());
        let cwd = workspace.assigned_cwd(&id);
        assert_eq!(cwd.file_name().unwrap(), id.as_str());
        workspace.create_assigned(&cwd, &toml::Table::new(), &config).unwrap();
        fs::write(cwd.join("keep"), "existing data").unwrap();
        assert!(workspace.create_assigned(&cwd, &toml::Table::new(), &config).is_err());
        assert_eq!(fs::read_to_string(cwd.join("keep")).unwrap(), "existing data");
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&cwd).unwrap().permissions().mode() & 0o777, 0o700);
        }
    }

    #[test]
    fn explicit_workspace_must_exist_and_be_a_directory() {
        let home = tempfile::tempdir().unwrap();
        let path = home.path().join("missing");
        assert!(Workspace::prepare(Some(&path), true).is_err());
        fs::write(&path, "not a directory").unwrap();
        assert!(Workspace::prepare(Some(&path), true).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn explicit_symlinks_cannot_alias_a_siblings_relative_workspace() {
        let home = tempfile::tempdir().unwrap();
        let project = home.path().join("project");
        fs::create_dir(&project).unwrap();
        let alias = home.path().join("alias");
        std::os::unix::fs::symlink(&project, &alias).unwrap();
        let workspace = Workspace::prepare(Some(&alias), true).unwrap();
        let document = toml::from_str("[[bot]]\ncwd='project'").unwrap();
        assert!(workspace.check_explicit(&document, &home.path().join("bridge.toml")).is_err());
        assert_eq!(workspace.assigned_cwd("bot_new"), project.canonicalize().unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn managed_workspace_rejects_a_preexisting_symlink() {
        let home = tempfile::tempdir().unwrap();
        let workspace = Workspace::managed(&home.path().join("bots")).unwrap();
        let cwd = workspace.assigned_cwd("bot_new");
        std::os::unix::fs::symlink(home.path(), &cwd).unwrap();
        assert!(workspace.create_assigned(&cwd, &toml::Table::new(), &home.path().join("bridge.toml")).is_err());
        assert!(fs::symlink_metadata(cwd).unwrap().file_type().is_symlink());
    }
}
