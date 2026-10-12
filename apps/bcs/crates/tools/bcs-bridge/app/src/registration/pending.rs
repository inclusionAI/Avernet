//! Retry identity is durable before the first POST. No credentials are stored.
use std::{fs::{self, OpenOptions}, io::Write, path::{Path, PathBuf}};
use anyhow::{bail, Context};
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize)]
struct Pending {
    request: String,
    slug: String,
    // Older pending files may already represent an unknown POST outcome.
    #[serde(default = "submitted_default")]
    submitted: bool,
}
fn submitted_default() -> bool { true }
fn file(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned(); name.push(".pending.json"); name.into()
}

pub(super) fn prepare(path: &Path, request: &str, explicit: Option<&str>) -> anyhow::Result<String> {
    let pending_path = file(path);
    if pending_path.exists() {
        if !fs::symlink_metadata(&pending_path)?.file_type().is_file() { bail!("Pending registration must be a regular file"); }
        let saved: Pending = serde_json::from_slice(&fs::read(&pending_path)?)
            .map_err(|_| anyhow::anyhow!("Invalid pending registration; review it before retrying"))?;
        if saved.request != request || explicit.is_some_and(|slug| slug != saved.slug) {
            if saved.submitted {
                bail!("Another registration is pending; retry the same command or review the pending file first");
            }
            fs::remove_file(&pending_path)?;
            return prepare(path, request, explicit);
        }
        return Ok(saved.slug);
    }
    let slug = explicit.map(str::to_string).unwrap_or_else(|| format!("bot-{}", uuid::Uuid::new_v4().simple()));
    // UUID slug uses 36 characters; keep the public 32-byte slug limit.
    let slug = if explicit.is_none() { slug[..28].to_owned() } else { slug };
    if slug.is_empty() || slug.len() > 32 || !slug.as_bytes()[0].is_ascii_alphanumeric()
        || slug.bytes().any(|b| !(b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')) {
        bail!("--bot-slug must be 1-32 lowercase letters, digits, '_' or '-', starting with a letter or digit");
    }
    let mut options = OpenOptions::new(); options.write(true).create_new(true);
    #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
    let mut output = options.open(&pending_path).context("Cannot save pending registration")?;
    output.write_all(&serde_json::to_vec(&Pending { request: request.into(), slug: slug.clone(), submitted: false })?)?;
    output.sync_all()?;
    sync_directory(&pending_path)?;
    Ok(slug)
}

pub(super) fn submitted(path: &Path) -> anyhow::Result<()> {
    let target = file(path);
    let mut record: Pending = serde_json::from_slice(&fs::read(&target)?)?;
    record.submitted = true;
    let mut pending = tempfile::NamedTempFile::new_in(target.parent().unwrap_or(Path::new(".")))?;
    pending.write_all(&serde_json::to_vec(&record)?)?;
    pending.as_file().sync_all()?;
    pending.persist(&target).map_err(|_| anyhow::anyhow!("Cannot persist submitted registration"))?;
    sync_directory(&target)?;
    Ok(())
}

fn sync_directory(path: &Path) -> anyhow::Result<()> {
    #[cfg(unix)]
    fs::File::open(path.parent().unwrap_or(Path::new(".")))?.sync_all()?;
    Ok(())
}

pub(super) fn complete(path: &Path) -> anyhow::Result<()> {
    fs::remove_file(file(path)).context("Configuration saved, but pending registration cleanup failed")
}
