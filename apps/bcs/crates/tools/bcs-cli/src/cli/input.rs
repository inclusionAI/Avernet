use anyhow::{Result, anyhow};

/// Parse a `--input` / `--meta` style argument: a `@file.json` reference is
/// loaded from disk; anything else is parsed directly as JSON. The leading
/// `@` is *only* a file marker when followed by a non-empty path.
pub(super) fn parse_json_arg(raw: &str) -> Result<serde_json::Value> {
    if let Some(path) = raw.strip_prefix('@') {
        if path.is_empty() {
            return Err(anyhow!("'@' must be followed by a file path"));
        }
        let content =
            std::fs::read_to_string(path).map_err(|e| anyhow!("Failed to read {}: {}", path, e))?;
        serde_json::from_str(&content).map_err(|e| anyhow!("Invalid JSON in {}: {}", path, e))
    } else {
        serde_json::from_str(raw).map_err(|e| anyhow!("Invalid JSON literal: {}", e))
    }
}
