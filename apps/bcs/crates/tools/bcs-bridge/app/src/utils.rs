//! Shared filesystem checks for bridge commands.
use std::path::{Path, PathBuf};

use anyhow::{Context, bail};

/// Resolve an installed engine executable without launching it: `override_bin`
/// when given, otherwise `default_bin` on `PATH`.
pub fn resolve_engine_bin(override_bin: Option<&Path>, default_bin: &str) -> anyhow::Result<PathBuf> {
    if let Some(path) = override_bin {
        let bin = absolute_path(path).context("Cannot resolve --engine-bin; specify the path to an installed engine executable")?;
        if !is_executable_file(&bin) {
            bail!("--engine-bin must point to an executable file; check the engine installation and execute permissions");
        }
        return Ok(bin);
    }
    if let Some(search_path) = std::env::var_os("PATH") {
        let name = format!("{default_bin}{}", std::env::consts::EXE_SUFFIX);
        for directory in std::env::split_paths(&search_path) {
            let candidate = directory.join(&name);
            if is_executable_file(&candidate) {
                return absolute_path(&candidate).with_context(|| format!("Cannot resolve {default_bin} from PATH; check the installation or use --engine-bin"));
            }
        }
    }
    bail!("Cannot find an executable {default_bin} in PATH; install it and add it to PATH, or specify --engine-bin /path/to/{default_bin}");
}

fn absolute_path(path: &Path) -> anyhow::Result<PathBuf> {
    if path.is_absolute() { Ok(path.to_path_buf()) }
    else { Ok(std::env::current_dir().context("Cannot read current directory")?.join(path)) }
}

fn is_executable_file(path: &Path) -> bool {
    let Ok(metadata) = path.metadata() else { return false; };
    if !metadata.is_file() { return false; }
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    true
}
