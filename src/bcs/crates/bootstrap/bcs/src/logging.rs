//! Configurable logging system for BCS.
//!
//! Supports:
//! - Console output with per-module log levels
//! - Custom tag-based log level control (e.g. `target: "audit"`)
//! - Multiple file outputs with target-based routing
//! - Daily log rotation: current log is `bcs.log`, rotated to `bcs.log.2026-04-07`
//! - Automatic cleanup of old log files (`max_keep_days`)

use crate::config::{LogOutputConfig, LogOutputFormat, LoggingConfig};
use std::ffi::OsStr;
use std::fs::{self, File, OpenOptions};
use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};
use time::macros::format_description;
use tracing::Level;
use tracing_subscriber::{
    filter::{LevelFilter, Targets}, fmt::time::LocalTime, fmt::MakeWriter,
    layer::SubscriberExt, util::SubscriberInitExt,
    EnvFilter, Layer,
};

/// Parse a log level string into a `tracing::Level`.
fn parse_level(s: &str) -> Level {
    match s.to_lowercase().as_str() {
        "trace" => Level::TRACE,
        "debug" => Level::DEBUG,
        "warn" => Level::WARN,
        "error" => Level::ERROR,
        _ => Level::INFO,
    }
}

/// Expand `~` prefix to the user's home directory.
fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") || path == "~" {
        if let Some(home) = std::env::var_os("HOME") {
            return path.replacen('~', &home.to_string_lossy(), 1);
        }
    }
    path.to_string()
}

/// Build an `EnvFilter` from config: default_level + modules + tags + RUST_LOG overlay.
fn build_env_filter(config: &LoggingConfig) -> EnvFilter {
    let mut directives = vec![config.default_level.clone()];
    for (target, level) in &config.modules {
        directives.push(format!("{target}={level}"));
    }
    for (tag, level) in &config.tags {
        directives.push(format!("{tag}={level}"));
    }
    if let Ok(rust_log) = std::env::var("RUST_LOG") {
        directives.push(rust_log);
    }
    EnvFilter::new(directives.join(","))
}

/// Build a file-output target filter from an output config.
///
/// `targets = ["*"]` includes all targets for the output level. Prefixing a
/// target with `!` excludes it from that output, e.g. `["*", "!bcs_chat_digest"]`.
fn build_output_targets_filter(output: &LogOutputConfig) -> Targets {
    let level = parse_level(&output.level);
    let mut filter = Targets::new();

    for target in &output.targets {
        let target = target.trim();
        if target.is_empty() {
            continue;
        }

        if let Some(excluded) = target.strip_prefix('!') {
            let excluded = excluded.trim();
            if excluded.is_empty() {
                continue;
            }
            if excluded == "*" {
                filter = filter.with_default(LevelFilter::OFF);
            } else {
                filter = filter.with_target(excluded, LevelFilter::OFF);
            }
        } else if target == "*" {
            filter = filter.with_default(level);
        } else {
            filter = filter.with_target(target, level);
        }
    }

    filter
}

/// Get today's date string in local time (YYYY-MM-DD).
fn today_local() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}

fn console_ansi_enabled() -> bool {
    let no_color = std::env::var_os("NO_COLOR");
    console_ansi_enabled_for(std::io::stdout().is_terminal(), no_color.as_deref())
}

fn console_ansi_enabled_for(is_terminal: bool, no_color: Option<&OsStr>) -> bool {
    is_terminal && no_color.map_or(true, OsStr::is_empty)
}

// ─── RotatingFileWriter ─────────────────────────────────────────────────────

/// A daily-rotating file writer.
///
/// Current log is always `{dir}/{file_name}` (no date suffix).
/// On date change, renames to `{dir}/{file_name}.{YYYY-MM-DD}` and creates a new file.
#[derive(Clone)]
struct RotatingFileWriter {
    inner: Arc<Mutex<RotatingInner>>,
}

struct RotatingInner {
    dir: PathBuf,
    file_name: String,
    current_date: String,
    file: File,
}

impl RotatingFileWriter {
    fn new(dir: &Path, file_name: &str) -> Self {
        let current_date = today_local();
        let file_path = dir.join(file_name);
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)
            .unwrap_or_else(|e| {
                panic!("Failed to open log file '{}': {}", file_path.display(), e)
            });

        Self {
            inner: Arc::new(Mutex::new(RotatingInner {
                dir: dir.to_path_buf(),
                file_name: file_name.to_string(),
                current_date,
                file,
            })),
        }
    }
}

/// Writer guard returned by `MakeWriter`.
struct RotatingWriterGuard<'a>(std::sync::MutexGuard<'a, RotatingInner>);

impl<'a> Write for RotatingWriterGuard<'a> {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let inner = &mut *self.0;
        let today = today_local();
        if today != inner.current_date {
            let current_path = inner.dir.join(&inner.file_name);
            let rotated = inner.dir.join(format!("{}.{}", inner.file_name, inner.current_date));

            // Close current file, rename, open new
            let placeholder = File::options().append(true).open("/dev/null");
            if let Ok(tmp) = placeholder {
                let old = std::mem::replace(&mut inner.file, tmp);
                drop(old);
                let _ = fs::rename(&current_path, &rotated);
                if let Ok(new_file) = OpenOptions::new().create(true).append(true).open(&current_path) {
                    inner.file = new_file;
                }
            }
            inner.current_date = today;
        }
        inner.file.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.0.file.flush()
    }
}

impl<'a> MakeWriter<'a> for RotatingFileWriter {
    type Writer = RotatingWriterGuard<'a>;

    fn make_writer(&'a self) -> Self::Writer {
        RotatingWriterGuard(self.inner.lock().unwrap_or_else(|e| e.into_inner()))
    }
}

// ─── Init ───────────────────────────────────────────────────────────────────

/// Initialize the tracing subscriber based on `LoggingConfig`.
///
/// Two code paths:
/// - Console-only (no file outputs): uses `tracing_subscriber::fmt()` directly.
/// - Console + file outputs: uses registry with per-layer filters.
pub fn init(config: &LoggingConfig) {
    let timer = LocalTime::new(format_description!(
        "[year]-[month]-[day] [hour]:[minute]:[second]"
    ));

    if config.outputs.is_empty() {
        if config.console {
            tracing_subscriber::fmt()
                .with_timer(timer)
                .with_ansi(console_ansi_enabled())
                .with_env_filter(build_env_filter(config))
                .init();
        }
        return;
    }

    let file_layers: Vec<Box<dyn Layer<_> + Send + Sync>> = config
        .outputs
        .iter()
        .filter_map(|output| {
            let path = expand_tilde(&output.path);
            let dir = PathBuf::from(&path);
            if let Err(e) = fs::create_dir_all(&dir) {
                eprintln!(
                    "[logging] WARNING: failed to create log directory '{}': {}. Output '{}' disabled.",
                    path, e, output.name
                );
                return None;
            }

            let writer = RotatingFileWriter::new(&dir, &output.file);

            let filter = build_output_targets_filter(output);
            match output.format {
                LogOutputFormat::Text => Some(
                    tracing_subscriber::fmt::layer()
                        .with_writer(writer)
                        .with_timer(timer.clone())
                        .with_ansi(false)
                        .with_filter(filter)
                        .boxed(),
                ),
                LogOutputFormat::Json => Some(
                    tracing_subscriber::fmt::layer()
                        .json()
                        .flatten_event(true)
                        .with_current_span(false)
                        .with_span_list(false)
                        .with_writer(writer)
                        .with_timer(timer.clone())
                        .with_ansi(false)
                        .with_filter(filter)
                        .boxed(),
                ),
            }
        })
        .collect();

    let console_layer = if config.console {
        Some(
            tracing_subscriber::fmt::layer()
                .with_timer(timer.clone())
                .with_ansi(console_ansi_enabled())
                .with_filter(build_env_filter(config)),
        )
    } else {
        None
    };

    tracing_subscriber::registry()
        .with(console_layer)
        .with(file_layers)
        .init();
}

// ─── Cleanup ────────────────────────────────────────────────────────────────

/// Spawn a background task that periodically cleans up old log files.
pub fn spawn_cleanup_task(outputs: Vec<LogOutputConfig>) {
    let outputs: Vec<_> = outputs.into_iter().filter(|o| o.max_keep_days > 0).collect();

    if outputs.is_empty() {
        return;
    }

    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(3600));
        loop {
            interval.tick().await;
            for output in &outputs {
                cleanup_old_logs(output);
            }
        }
    });
}

/// Scan a directory and remove log files older than `max_keep_days`.
fn cleanup_old_logs(output: &LogOutputConfig) {
    let path = expand_tilde(&output.path);
    let cutoff = SystemTime::now() - Duration::from_secs(output.max_keep_days * 86400);

    let entries = match fs::read_dir(&path) {
        Ok(e) => e,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();

        // Rotated files: {prefix}.2026-04-07. Skip current file (no date suffix).
        if !name_str.starts_with(&output.file) || name_str == output.file {
            continue;
        }

        let modified = match entry.metadata().and_then(|m| m.modified()) {
            Ok(t) => t,
            Err(_) => continue,
        };

        if modified < cutoff {
            if let Err(e) = fs::remove_file(entry.path()) {
                tracing::warn!(
                    file = %entry.path().display(),
                    error = %e,
                    "Failed to remove old log file"
                );
            } else {
                tracing::info!(
                    file = %entry.path().display(),
                    "Removed old log file"
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;
    use tracing_subscriber::layer::SubscriberExt;

    #[test]
    fn console_ansi_only_when_stdout_is_terminal_and_no_color_absent_or_empty() {
        assert!(console_ansi_enabled_for(true, None));
        assert!(console_ansi_enabled_for(true, Some(OsStr::new(""))));
        assert!(!console_ansi_enabled_for(true, Some(OsStr::new("1"))));
        assert!(!console_ansi_enabled_for(false, None));
        assert!(!console_ansi_enabled_for(false, Some(OsStr::new(""))));
    }

    #[test]
    fn wildcard_output_can_exclude_chat_digest_target() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_string_lossy().to_string();
        let main_output = LogOutputConfig {
            name: "main".to_string(),
            path: path.clone(),
            file: "bcs.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["*".to_string(), "!bcs_chat_digest".to_string()],
            max_keep_days: 7,
        };
        let digest_output = LogOutputConfig {
            name: "chat-digest".to_string(),
            path,
            file: "bcs-chat-digest.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["bcs_chat_digest".to_string()],
            max_keep_days: 7,
        };

        let subscriber = tracing_subscriber::registry()
            .with(
                tracing_subscriber::fmt::layer()
                    .with_writer(RotatingFileWriter::new(dir.path(), &main_output.file))
                    .with_ansi(false)
                    .with_filter(build_output_targets_filter(&main_output)),
            )
            .with(
                tracing_subscriber::fmt::layer()
                    .with_writer(RotatingFileWriter::new(dir.path(), &digest_output.file))
                    .with_ansi(false)
                    .with_filter(build_output_targets_filter(&digest_output)),
            );

        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            tracing::info!(target: "bcs_chat_digest", "endpoint=bot_chat,success=true");
            tracing::info!(target: "bcs_http::routes", "ordinary bcs log");
        });

        let main = std::fs::read_to_string(dir.path().join("bcs.log")).unwrap();
        let digest = std::fs::read_to_string(dir.path().join("bcs-chat-digest.log")).unwrap();

        assert!(main.contains("ordinary bcs log"));
        assert!(!main.contains("endpoint=bot_chat"));
        assert!(digest.contains("endpoint=bot_chat"));
        assert!(!digest.contains("ordinary bcs log"));
    }

    #[test]
    fn json_output_flattens_message_log_fields() {
        let dir = tempfile::tempdir().unwrap();
        let output = LogOutputConfig {
            name: "messages".to_string(),
            path: dir.path().to_string_lossy().to_string(),
            file: "bcs-messages.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Json,
            targets: vec!["bcs_message".to_string()],
            max_keep_days: 7,
        };

        let subscriber = tracing_subscriber::registry().with(
            tracing_subscriber::fmt::layer()
                .json()
                .flatten_event(true)
                .with_current_span(false)
                .with_span_list(false)
                .with_writer(RotatingFileWriter::new(dir.path(), &output.file))
                .with_ansi(false)
                .with_filter(build_output_targets_filter(&output)),
        );

        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            tracing::info!(
                target: "bcs_message",
                session_id = "s1",
                bot_id = "b1",
                run_id = "r1",
                event_type = "bot_accept"
            );
        });

        let content = std::fs::read_to_string(dir.path().join("bcs-messages.log")).unwrap();
        let line = content.lines().next().expect("json log line should be written");
        let json: serde_json::Value = serde_json::from_str(line).expect("message log line is json");

        assert_eq!(json["session_id"], "s1");
        assert_eq!(json["bot_id"], "b1");
        assert_eq!(json["run_id"], "r1");
        assert_eq!(json["event_type"], "bot_accept");
    }
}
