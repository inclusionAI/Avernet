use std::path::{Path, PathBuf};

use anyhow::bail;

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
