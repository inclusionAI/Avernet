use std::path::{Path, PathBuf};

use anyhow::{Context, bail};

/// Resolve an implicit start without reading credentials or configuration contents.
pub fn start_configuration(home: &Path) -> anyhow::Result<PathBuf> {
    let default = home.join(".bcn-bridge/bridge.toml");
    if default.try_exists().context("Cannot inspect default bridge configuration")? { return Ok(default); }
    let root = home.join(".bcn-bridge/profiles");
    let entries = match std::fs::read_dir(&root) {
        Ok(entries) => Some(entries),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error).context("Cannot list bridge profiles"),
    };
    let mut profiles = Vec::new();
    if let Some(entries) = entries {
        for entry in entries {
            let entry = entry.context("Cannot inspect bridge profile directory")?;
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue; };
            let Ok(path) = configuration(home, name) else { continue; };
            match std::fs::metadata(&path) {
                Ok(metadata) if metadata.is_file() => profiles.push((name.to_owned(), path)),
                Ok(_) => {},
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
                Err(error) => return Err(error).context("Cannot inspect bridge profile configuration"),
            }
        }
    }
    profiles.sort_by(|a, b| a.0.cmp(&b.0));
    match profiles.as_slice() {
        [(name, path)] => {
            eprintln!("Using bridge profile: {name}");
            Ok(path.clone())
        }
        [] => bail!("No bridge configuration found at {} or in {}. Complete Bot onboarding first, or pass --profile <name> or --config <path>.", default.display(), root.display()),
        _ => {
            let choices = profiles.iter().map(|(name, _)| format!("  bcs-bridge --profile {name} start --daemon")).collect::<Vec<_>>().join("\n");
            bail!("Multiple bridge profiles found; choose one with --profile:\n{choices}")
        }
    }
}

pub fn configuration(home: &Path, name: &str) -> anyhow::Result<PathBuf> {
    if name.is_empty() || name.len() > 64
        || !name.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
    {
        bail!("Profile names must contain 1-64 letters, digits, '-' or '_'");
    }
    Ok(home.join(".bcn-bridge/profiles").join(name).join("bridge.toml"))
}

pub fn active_name<'a>(home_runtime: &Path, config: &'a Path) -> Option<&'a str> {
    let relative = config.strip_prefix(home_runtime.join("profiles")).ok()?;
    let mut components = relative.components();
    let name = components.next()?.as_os_str().to_str()?;
    if components.next()?.as_os_str() != "bridge.toml" || components.next().is_some() { return None; }
    Some(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_paths_are_isolated_and_cannot_escape_the_profile_directory() {
        let home = Path::new("/tmp/home");
        assert_ne!(configuration(home, "local").unwrap(), configuration(home, "pre").unwrap());
        assert_eq!(configuration(home, "pre").unwrap(), home.join(".bcn-bridge/profiles/pre/bridge.toml"));
        for name in ["", "../pre", "/tmp/pre", "pre/prod", ".", "a b"] { assert!(configuration(home, name).is_err()); }
        assert!(configuration(home, &"a".repeat(65)).is_err());
        assert_eq!(active_name(&home.join(".bcn-bridge"), &configuration(home, "pre").unwrap()), Some("pre"));
    }
}
