//! Session file and BCS API URL resolution shared by both crate roots.
//!
//! `src/lib.rs` and `src/main.rs` are two targets of one package, so
//! `pub(crate)` cannot cross them. Each root loads this single file as its own
//! private `shared_env` module, and `SessionInfo` still comes from the root that
//! owns it through `super::SessionInfo`.
//!
//! Keep CLI argument types, log targets, network probing, and authentication
//! flow out of this module; those stay binary-side in `cli::environment`.
//!
//! Rules for editing: reach root items only through `super::`, never `crate::`,
//! because the internal overlay loads this file beneath a `#[path]`-imported
//! root (see the overlay note on `main.rs::run`). Every item here must be needed
//! by BOTH crate roots; bin-only behavior belongs in `cli::environment`. Nothing
//! in this file may be `pub`; `pub(super)` is the widest visibility used.

use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow};

use super::SessionInfo;

fn session_file_path(data_dir: impl AsRef<Path>) -> PathBuf {
    data_dir.as_ref().join(".bcs").join("session.json")
}

pub(super) fn get_optional_session_file_path() -> Option<PathBuf> {
    std::env::var("BOT_DATA_DIR").ok().map(session_file_path)
}

pub(super) fn load_session_info_from_path(session_file: &Path) -> Result<Option<SessionInfo>> {
    if !session_file.exists() {
        return Ok(None);
    }

    let content = std::fs::read_to_string(session_file)
        .map_err(|e| anyhow!("Failed to read session file {:?}: {}", session_file, e))?;

    let session: SessionInfo = serde_json::from_str(&content)
        .map_err(|e| anyhow!("Failed to parse session file {:?}: {}", session_file, e))?;

    Ok(Some(session))
}

pub(super) fn load_optional_session_info() -> Result<Option<SessionInfo>> {
    let Some(session_file) = get_optional_session_file_path() else {
        return Ok(None);
    };
    load_session_info_from_path(&session_file)
}

pub(super) fn normalize_bcs_api_url(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }

    let mut url = reqwest::Url::parse(trimmed).ok()?;
    match url.scheme() {
        "ws" => url.set_scheme("http").ok()?,
        "wss" => url.set_scheme("https").ok()?,
        "http" | "https" => {}
        _ => return None,
    }

    url.set_query(None);
    url.set_fragment(None);

    let normalized_path = match url.path() {
        "/" | "" => "/".to_string(),
        path if path.ends_with("/ws/bot") => {
            let stripped = path.trim_end_matches("/ws/bot").trim_end_matches('/');
            if stripped.is_empty() {
                "/".to_string()
            } else {
                stripped.to_string()
            }
        }
        path => path.trim_end_matches('/').to_string(),
    };
    url.set_path(&normalized_path);

    Some(url.as_str().trim_end_matches('/').to_string())
}

pub(super) fn normalize_url_from_source(raw: String, source: &str) -> Result<String> {
    normalize_bcs_api_url(&raw)
        .ok_or_else(|| anyhow!("Invalid BCS API URL from {}: {}", source, raw))
}

pub(super) fn resolve_env_bcs_url() -> Result<Option<String>> {
    if let Ok(url) = std::env::var("BCS_API_BASE_URL") {
        return normalize_url_from_source(url, "BCS_API_BASE_URL").map(Some);
    }

    if let Ok(url) = std::env::var("MOLTIS_BCS_URL") {
        return normalize_url_from_source(url, "MOLTIS_BCS_URL").map(Some);
    }

    Ok(None)
}

pub(super) fn resolve_session_bcs_url() -> Result<Option<String>> {
    let Some(session) = load_optional_session_info()? else {
        return Ok(None);
    };

    let Some(raw_url) = session
        .api_base_url
        .as_deref()
        .or(session.bcs_url.as_deref())
    else {
        return Ok(None);
    };

    normalize_url_from_source(raw_url.to_string(), "$BOT_DATA_DIR/.bcs/session.json").map(Some)
}
