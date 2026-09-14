//! Configurable logging system for BCS.
//!
//! Supports:
//! - Console output with per-module log levels
//! - Custom tag-based log level control (e.g. `target: "audit"`)
//! - Multiple file outputs with target-based routing
//! - Daily log rotation: current log is `bcs.log`, rotated to `bcs.log.2026-04-07`
//! - Automatic cleanup of old log files (`max_keep_days`)

use crate::config::{LogOutputConfig, LogOutputFormat, LoggingConfig};
use opentelemetry_sdk::trace::SdkTracer;
use std::ffi::OsStr;
use std::fs::{self, File, OpenOptions};
use std::io::{IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::sync::{OnceLock, atomic::{AtomicU64, Ordering}};
use std::time::{Duration, SystemTime};
use time::macros::format_description;
use tracing::Level;
use tracing_subscriber::{
    filter::{LevelFilter, Targets}, fmt::time::LocalTime, fmt::MakeWriter,
    layer::SubscriberExt, util::SubscriberInitExt,
    EnvFilter, Layer,
};

pub(crate) const DELIVERY_MONITOR_TARGET: &str = "bcs_message_delivery_monitor";
const DELIVERY_MONITOR_OUTPUT: &str = "message-delivery";
static DELIVERY_MONITOR_DROPS: OnceLock<tracing_appender::non_blocking::ErrorCounter> = OnceLock::new();
static DELIVERY_MONITOR_ERRORS: AtomicU64 = AtomicU64::new(0);

pub(crate) fn delivery_monitor_statistics() -> (usize, u64) {
    (DELIVERY_MONITOR_DROPS.get().map_or(0, |counter| counter.dropped_lines()), DELIVERY_MONITOR_ERRORS.load(Ordering::Relaxed))
}

/// Older explicit output lists predate the monitor. Register its normal logging
/// output beside the main/first file unless the operator supplied one explicitly.
pub(crate) fn effective_outputs(config: &LoggingConfig) -> Vec<LogOutputConfig> {
    let mut outputs = config.outputs.clone();
    if !outputs.iter().any(|output| output.name == DELIVERY_MONITOR_OUTPUT) {
        let base = outputs.iter().find(|output| output.name == "main").or(outputs.first());
        outputs.push(LogOutputConfig {
            name: DELIVERY_MONITOR_OUTPUT.into(),
            path: base.map_or_else(|| "./logs".into(), |output| output.path.clone()),
            file: "message-delivery.log".into(), level: "info".into(), rotation: "daily".into(),
            format: LogOutputFormat::Raw, targets: vec![DELIVERY_MONITOR_TARGET.into()],
            max_keep_days: base.map_or(7, |output| output.max_keep_days),
        });
    }
    outputs
}

/// Message-only format for positional monitor records. All non-message fields,
/// span metadata and the ordinary timestamp/level/target prefix are omitted.
struct RawMessageFormat;
impl<S, N> tracing_subscriber::fmt::FormatEvent<S, N> for RawMessageFormat
where
    S: tracing::Subscriber + for<'a> tracing_subscriber::registry::LookupSpan<'a>,
    N: for<'a> tracing_subscriber::fmt::FormatFields<'a> + 'static,
{
    fn format_event(&self, _ctx: &tracing_subscriber::fmt::FmtContext<'_, S, N>, mut writer: tracing_subscriber::fmt::format::Writer<'_>, event: &tracing::Event<'_>) -> std::fmt::Result {
        #[derive(Default)]
        struct Message(String);
        impl tracing::field::Visit for Message {
            fn record_debug(&mut self, field: &tracing::field::Field, value: &dyn std::fmt::Debug) {
                if field.name() == "message" { self.0 = format!("{value:?}"); }
            }
            fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
                if field.name() == "message" { self.0 = value.to_string(); }
            }
        }
        let mut message = Message::default(); event.record(&mut message);
        writeln!(writer, "{}", message.0.trim_end_matches('\n'))
    }
}

struct ObservedMonitorWriter(RotatingFileWriter);
impl Write for ObservedMonitorWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let result = self.0.write(bytes);
        if let Err(error) = &result {
            DELIVERY_MONITOR_ERRORS.fetch_add(1, Ordering::Relaxed);
            // Avoid recursively logging from the logging writer thread.
            eprintln!("[logging] message-delivery write/rotation failed: {error}");
        }
        result
    }
    fn flush(&mut self) -> std::io::Result<()> {
        let result = self.0.flush();
        if let Err(error) = &result {
            DELIVERY_MONITOR_ERRORS.fetch_add(1, Ordering::Relaxed);
            eprintln!("[logging] message-delivery flush failed: {error}");
        }
        result
    }
}

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
pub(crate) fn expand_tilde(path: &str) -> String {
    if path.starts_with("~/") || path == "~" {
        if let Some(home) = std::env::var_os("HOME") {
            return path.replacen('~', &home.to_string_lossy(), 1);
        }
    }
    path.to_string()
}

/// Build an `EnvFilter` from config: default_level + modules + tags + RUST_LOG overlay.
fn build_env_filter(config: &LoggingConfig) -> EnvFilter {
    build_env_filter_with_overlay(config, std::env::var("RUST_LOG").ok().as_deref())
}

fn build_env_filter_with_overlay(config: &LoggingConfig, rust_log: Option<&str>) -> EnvFilter {
    // SQL/run stage profiling is high-volume: global DEBUG must not enable it.
    // An explicit tag/module or RUST_LOG target directive can opt back in.
    let mut directives = vec![config.default_level.clone(), "bcs_reply_profile=off".into()];
    for (target, level) in &config.modules {
        directives.push(format!("{target}={level}"));
    }
    for (tag, level) in &config.tags {
        directives.push(format!("{tag}={level}"));
    }
    if let Some(rust_log) = rust_log {
        directives.push(rust_log.to_owned());
    }
    // Dedicated positional records must not leak to console, even with RUST_LOG.
    directives.push(format!("{DELIVERY_MONITOR_TARGET}=off"));
    EnvFilter::new(directives.join(","))
}

/// Build a file-output target filter from an output config.
///
/// `targets = ["*"]` includes all targets for the output level. Prefixing a
/// target with `!` excludes it from that output, e.g. `["*", "!bcs_chat_digest"]`.
fn build_output_targets_filter(output: &LogOutputConfig) -> Targets {
    let level = parse_level(&output.level);
    if output.name == DELIVERY_MONITOR_OUTPUT {
        return Targets::new().with_target(DELIVERY_MONITOR_TARGET, level);
    }
    // Wildcard DEBUG outputs exclude profiling unless the target is named.
    let mut filter = Targets::new().with_target("bcs_reply_profile", LevelFilter::OFF);

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

    filter.with_target(DELIVERY_MONITOR_TARGET, LevelFilter::OFF)
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

            inner.file.flush()?;
            let rotated = if rotated.exists() {
                inner.dir.join(format!("{}.{}.{}", inner.file_name, inner.current_date, uuid::Uuid::new_v4().simple()))
            } else { rotated };
            fs::rename(&current_path, &rotated)?;
            match OpenOptions::new().create(true).append(true).open(&current_path) {
                Ok(file) => inner.file = file,
                Err(error) => { let _ = fs::rename(&rotated, &current_path); return Err(error); }
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

impl Write for RotatingFileWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.make_writer().write(bytes)
    }
    fn flush(&mut self) -> std::io::Result<()> { self.make_writer().flush() }
}

/// Keep these guards alive until the runtime has stopped, then flush queued logs.
#[must_use]
pub struct LoggingGuard {
    _workers: Vec<tracing_appender::non_blocking::WorkerGuard>,
}

fn buffered_writer(writer: impl Write + Send + 'static) -> (tracing_appender::non_blocking::NonBlocking, tracing_appender::non_blocking::WorkerGuard) {
    tracing_appender::non_blocking::NonBlockingBuilder::default()
        .buffered_lines_limit(4096)
        .lossy(true)
        .finish(writer)
}

// ─── Init ───────────────────────────────────────────────────────────────────

fn timer_for_output<F: Clone>(
    output_name: &str,
    timer: &LocalTime<F>,
    millisecond_timer: &LocalTime<F>,
) -> LocalTime<F> {
    if output_name == "common-error" {
        millisecond_timer.clone()
    } else {
        timer.clone()
    }
}

/// Initialize the tracing subscriber based on `LoggingConfig`.
///
/// Console, file, and BCN OpenTelemetry output use independent layer filters.
pub fn init(config: &LoggingConfig, tracer: SdkTracer) -> LoggingGuard {
    let mut workers = Vec::new();
    let timer = LocalTime::new(format_description!(
        "[year]-[month]-[day] [hour]:[minute]:[second]"
    ));
    let millisecond_timer = LocalTime::new(format_description!(
        "[year]-[month]-[day] [hour]:[minute]:[second].[subsecond digits:3]"
    ));

    let outputs = effective_outputs(config);
    let file_layers: Vec<Box<dyn Layer<_> + Send + Sync>> = outputs
        .iter()
        .filter_map(|output| {
            let path = expand_tilde(&output.path);
            let dir = PathBuf::from(&path);
            if let Err(e) = fs::create_dir_all(&dir) {
                if output.name == DELIVERY_MONITOR_OUTPUT {
                    panic!("Failed to create delivery monitor log directory '{}': {}", path, e);
                }
                eprintln!(
                    "[logging] WARNING: failed to create log directory '{}': {}. Output '{}' disabled.",
                    path, e, output.name
                );
                return None;
            }

            let rotating = RotatingFileWriter::new(&dir, &output.file);
            let (writer, guard) = if output.name == DELIVERY_MONITOR_OUTPUT {
                let pair = buffered_writer(ObservedMonitorWriter(rotating));
                let _ = DELIVERY_MONITOR_DROPS.set(pair.0.error_counter());
                pair
            } else { buffered_writer(rotating) };
            workers.push(guard);

            let filter = build_output_targets_filter(output);
            let output_timer = timer_for_output(&output.name, &timer, &millisecond_timer);
            // Preserve the monitor's positional contract even for a legacy
            // explicit output that omitted format (which otherwise defaults text).
            let format = if output.name == DELIVERY_MONITOR_OUTPUT { LogOutputFormat::Raw } else { output.format };
            match format {
                LogOutputFormat::Raw => Some(
                    tracing_subscriber::fmt::layer()
                        .event_format(RawMessageFormat)
                        .with_writer(writer)
                        .with_ansi(false)
                        .with_filter(filter)
                        .boxed(),
                ),
                LogOutputFormat::Text => Some(
                    tracing_subscriber::fmt::layer()
                        .with_writer(writer)
                        .with_timer(output_timer)
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
                        .with_timer(output_timer)
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

    let otel_layer = tracing_opentelemetry::layer()
        .with_tracer(tracer)
        .with_filter(Targets::new().with_target("bcn_otel", LevelFilter::TRACE));

    tracing_subscriber::registry()
        .with(console_layer)
        .with(file_layers)
        .with(otel_layer)
        .init();
    LoggingGuard { _workers: workers }
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
                if output.name == DELIVERY_MONITOR_OUTPUT { DELIVERY_MONITOR_ERRORS.fetch_add(1, Ordering::Relaxed); }
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
    fn reply_profile_requires_explicit_target_even_at_global_debug() {
        for explicit in [false, true] {
            let mut config = LoggingConfig::default();
            config.default_level = "debug".into();
            if explicit { config.tags.insert("bcs_reply_profile".into(), "debug".into()); }
            let console = tracing_subscriber::registry().with(
                tracing_subscriber::fmt::layer().with_writer(std::io::sink).with_filter(build_env_filter_with_overlay(&config, None))
            );
            tracing::subscriber::with_default(console, || {
                assert_eq!(tracing::enabled!(target: "bcs_reply_profile", Level::DEBUG), explicit);
                assert!(tracing::enabled!(target: "ordinary_test", Level::DEBUG));
                assert!(!tracing::enabled!(target: DELIVERY_MONITOR_TARGET, Level::INFO));
            });
            let mut output = config.outputs[0].clone();
            output.name = "main".into(); output.level = "debug".into();
            output.targets = vec!["*".into()];
            if explicit { output.targets.push("bcs_reply_profile".into()); }
            let file = tracing_subscriber::registry().with(
                tracing_subscriber::fmt::layer().with_writer(std::io::sink).with_filter(build_output_targets_filter(&output))
            );
            tracing::subscriber::with_default(file, || {
                assert_eq!(tracing::enabled!(target: "bcs_reply_profile", Level::DEBUG), explicit);
                assert!(tracing::enabled!(target: "ordinary_test", Level::DEBUG));
                assert!(!tracing::enabled!(target: DELIVERY_MONITOR_TARGET, Level::INFO));
            });
        }
    }

    #[test]
    fn monitor_uses_existing_raw_layer_worker_and_excludes_other_outputs() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = LoggingConfig::default();
        config.outputs.retain(|output| output.name == DELIVERY_MONITOR_OUTPUT);
        let output = &mut config.outputs[0];
        output.path = dir.path().to_string_lossy().into_owned();
        let output = output.clone();
        let mut main = output.clone(); main.name = "main".into(); main.file = "bcs.log".into(); main.targets = vec!["*".into()]; main.format = LogOutputFormat::Text;
        let (writer, guard) = buffered_writer(RotatingFileWriter::new(dir.path(), &output.file));
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::fmt::layer().event_format(RawMessageFormat).with_writer(writer).with_filter(build_output_targets_filter(&output)))
            .with(tracing_subscriber::fmt::layer().with_ansi(false).with_writer(RotatingFileWriter::new(dir.path(), &main.file)).with_filter(build_output_targets_filter(&main)))
            .with(tracing_subscriber::fmt::layer().with_ansi(false).with_writer(RotatingFileWriter::new(dir.path(), "console.log")).with_filter(build_env_filter(&config)));
        let dispatch = tracing::Dispatch::new(subscriber);
        let rows = "1,1000,boot,0,0,0,0,0,1,3,0,0,0,1,1000\n1,1000,boot,0,0,0,0,0,2,1,0,0,0,1,1000";
        tracing::dispatcher::with_default(&dispatch, || {
            tracing::info!(target: DELIVERY_MONITOR_TARGET, ignored = "never-written", "{rows}");
            tracing::error!("ordinary message");
        });
        drop(guard);
        let monitor = fs::read_to_string(dir.path().join("message-delivery.log")).unwrap();
        assert_eq!(monitor, format!("{rows}\n"));
        assert!(monitor.lines().all(|line| line.split(',').count() == 15));
        for file in ["bcs.log", "console.log"] {
            let text = fs::read_to_string(dir.path().join(file)).unwrap();
            assert!(text.contains("ordinary message"), "{file}: {text:?}"); assert!(!text.contains("1,1000,boot"));
        }
    }

    #[test]
    fn monitor_output_reuses_explicit_config_or_registers_beside_main() {
        let mut config = LoggingConfig::default();
        config.outputs.retain(|output| output.name == DELIVERY_MONITOR_OUTPUT);
        config.outputs[0].path = "/configured/logs".into(); config.outputs[0].file = "custom-monitor.log".into();
        assert_eq!(effective_outputs(&config)[0].file, "custom-monitor.log");
        config.outputs[0].name = "main".into();
        let outputs = effective_outputs(&config);
        assert_eq!(outputs.len(), 2); assert_eq!(outputs[1].path, "/configured/logs");
        assert_eq!(outputs[1].file, "message-delivery.log"); assert_eq!(outputs[1].format, LogOutputFormat::Raw);
    }

    #[test]
    fn shared_rotating_writer_preserves_monitor_records_and_retention() {
        let dir = tempfile::tempdir().unwrap();
        let mut writer = RotatingFileWriter::new(dir.path(), "message-delivery.log");
        writer.write_all(b"old\n").unwrap();
        writer.inner.lock().unwrap().current_date = "2000-01-01".into();
        writer.write_all(b"new\n").unwrap(); writer.flush().unwrap();
        let rotated = dir.path().join("message-delivery.log.2000-01-01");
        assert_eq!(fs::read_to_string(&rotated).unwrap(), "old\n");
        assert_eq!(fs::read_to_string(dir.path().join("message-delivery.log")).unwrap(), "new\n");
        File::open(&rotated).unwrap().set_times(fs::FileTimes::new().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(1))).unwrap();
        let mut output = LoggingConfig::default().outputs.into_iter().find(|o| o.name == DELIVERY_MONITOR_OUTPUT).unwrap();
        output.path = dir.path().to_string_lossy().into_owned();
        cleanup_old_logs(&output);
        assert!(!rotated.exists()); assert!(dir.path().join("message-delivery.log").exists());
    }

    #[test]
    fn worker_guard_flushes_all_queued_file_lines() {
        let dir = tempfile::tempdir().unwrap();
        let (mut writer, guard) = buffered_writer(RotatingFileWriter::new(dir.path(), "buffered.log"));
        for _ in 0..100 { writeln!(writer, "queued-log-line").unwrap(); }
        drop(guard);
        let output = fs::read_to_string(dir.path().join("buffered.log")).unwrap();
        assert_eq!(output.lines().count(), 100);
        assert_eq!(writer.error_counter().dropped_lines(), 0);
    }

    #[test]
    fn stalled_sink_drops_and_counts_lines_without_blocking_callers() {
        struct BlockedWriter {
            entered: std::sync::mpsc::Sender<()>,
            release: std::sync::mpsc::Receiver<()>,
            first: bool,
        }
        impl Write for BlockedWriter {
            fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
                if self.first {
                    self.first = false;
                    self.entered.send(()).unwrap();
                    self.release.recv_timeout(Duration::from_secs(10)).unwrap();
                }
                Ok(bytes.len())
            }
            fn flush(&mut self) -> std::io::Result<()> { Ok(()) }
        }
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let (mut writer, guard) = buffered_writer(BlockedWriter { entered: entered_tx, release: release_rx, first: true });
        writeln!(writer, "first").unwrap();
        entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        for _ in 0..5000 { writeln!(writer, "queued").unwrap(); }
        assert!(writer.error_counter().dropped_lines() > 0);
        release_tx.send(()).unwrap();
        drop(guard);
    }

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
    fn error_is_written_to_main_and_common_error_outputs() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_string_lossy().to_string();
        let main_output = LogOutputConfig {
            name: "main".to_string(),
            path: path.clone(),
            file: "bcs.log".to_string(),
            level: "info".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["*".to_string()],
            max_keep_days: 7,
        };
        let common_error_output = LogOutputConfig {
            name: "common-error".to_string(),
            path,
            file: "common-error.log".to_string(),
            level: "error".to_string(),
            rotation: "daily".to_string(),
            format: LogOutputFormat::Text,
            targets: vec!["*".to_string()],
            max_keep_days: 7,
        };

        let timer = LocalTime::new(format_description!(
            "[year]-[month]-[day] [hour]:[minute]:[second]"
        ));
        let millisecond_timer = LocalTime::new(format_description!(
            "[year]-[month]-[day] [hour]:[minute]:[second].[subsecond digits:3]"
        ));
        let subscriber = tracing_subscriber::registry()
            .with(
                tracing_subscriber::fmt::layer()
                    .with_writer(RotatingFileWriter::new(dir.path(), &main_output.file))
                    .with_timer(timer_for_output(
                        &main_output.name,
                        &timer,
                        &millisecond_timer,
                    ))
                    .with_ansi(false)
                    .with_filter(build_output_targets_filter(&main_output)),
            )
            .with(
                tracing_subscriber::fmt::layer()
                    .with_writer(RotatingFileWriter::new(
                        dir.path(),
                        &common_error_output.file,
                    ))
                    .with_timer(timer_for_output(
                        &common_error_output.name,
                        &timer,
                        &millisecond_timer,
                    ))
                    .with_ansi(false)
                    .with_filter(build_output_targets_filter(&common_error_output)),
            );

        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            tracing::warn!(target: "bcs_http::routes", "warning stays in main only");
            tracing::error!(target: "bcs_http::routes", "error is duplicated");
        });

        let main = std::fs::read_to_string(dir.path().join("bcs.log")).unwrap();
        let common_error =
            std::fs::read_to_string(dir.path().join("common-error.log")).unwrap();

        assert!(main.contains("warning stays in main only"));
        assert!(main.contains("error is duplicated"));
        let main_error_line = main
            .lines()
            .find(|line| line.contains("error is duplicated"))
            .expect("main error log line should exist");
        let main_timestamp = main_error_line
            .get(..19)
            .expect("main timestamp should include seconds");
        assert!(
            chrono::NaiveDateTime::parse_from_str(main_timestamp, "%Y-%m-%d %H:%M:%S").is_ok(),
            "main timestamp should use YYYY-MM-DD HH:mm:ss: {main_timestamp}"
        );
        assert_eq!(
            main_error_line.as_bytes().get(19),
            Some(&b' '),
            "main timestamp should not include fractional seconds"
        );
        assert!(!common_error.contains("warning stays in main only"));
        assert!(common_error.contains("error is duplicated"));
        let common_error_line = common_error
            .lines()
            .find(|line| line.contains("error is duplicated"))
            .expect("common error log line should exist");
        let timestamp = common_error_line
            .get(..23)
            .expect("common error timestamp should include milliseconds");
        assert!(
            chrono::NaiveDateTime::parse_from_str(timestamp, "%Y-%m-%d %H:%M:%S%.3f").is_ok(),
            "common error timestamp should use YYYY-MM-DD HH:mm:ss.SSS: {timestamp}"
        );
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
    #[test]
    fn observation_file_duplicates_diagnostics_in_main_text_format() {
        let dir = tempfile::tempdir().unwrap();
        let output = LogOutputConfig {
            name: "observability".into(), path: dir.path().to_string_lossy().into(),
            file: "bcs-observability.log".into(), level: "info".into(), rotation: "daily".into(),
            format: LogOutputFormat::Text, targets: vec!["bcs_observation".into(), "bcs_http_access".into()],
            max_keep_days: 7,
        };
        let timer = LocalTime::new(format_description!("[year]-[month]-[day] [hour]:[minute]:[second]"));
        let millis = LocalTime::new(format_description!("[year]-[month]-[day] [hour]:[minute]:[second].[subsecond digits:3]"));
        let (main_writer, main_guard) = buffered_writer(RotatingFileWriter::new(dir.path(), "bcs.log"));
        let (observation_writer, observation_guard) = buffered_writer(RotatingFileWriter::new(dir.path(), &output.file));
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::fmt::layer().with_ansi(false).with_writer(main_writer)
                .with_timer(timer_for_output("main", &timer, &millis)))
            .with(tracing_subscriber::fmt::layer().with_ansi(false)
                .with_timer(timer_for_output(&output.name, &timer, &millis))
                .with_writer(observation_writer).with_filter(build_output_targets_filter(&output)));
        let dispatch = tracing::Dispatch::new(subscriber);
        tracing::dispatcher::with_default(&dispatch, || {
            tracing::warn!(target: "bcs_observation", request_id = "diagnostic-42", operation = "test.io",
                operation_id = "operation-42", process_instance_id = "process-42",
                duration_ms = 123.5, "bcs.operation.finished");
            tracing::info!(target: "bcs_http_access", request_id = "diagnostic-42", status = 200, "http.request.response_ready");
            tracing::info!(target: "unrelated_module", "ordinary business log");
        });
        drop(dispatch);
        drop(main_guard);
        drop(observation_guard);
        let main = fs::read_to_string(dir.path().join("bcs.log")).unwrap();
        assert!(main.contains("bcs.operation.finished") && main.contains("http.request.response_ready"));
        assert!(main.contains("ordinary business log"));
        let diagnostics = fs::read_to_string(dir.path().join(&output.file)).unwrap();
        let events: Vec<&str> = diagnostics.lines().collect();
        assert_eq!(events.len(), 2);
        for event in &events {
            assert!(chrono::NaiveDateTime::parse_from_str(&event[..19], "%Y-%m-%d %H:%M:%S").is_ok());
            assert_eq!(event.as_bytes().get(19), Some(&b' '), "diagnostic timestamp should match bcs.log");
            assert!(event.contains("request_id=\"diagnostic-42\""));
            assert!(!event.contains("trace_id=") && !event.contains("\u{1b}["));
            assert!(main.lines().any(|line| line.get(19..) == event.get(19..)), "diagnostic event should use the main log format");
        }
        assert!(events[0].contains(" WARN bcs_observation: bcs.operation.finished"));
        assert!(events[0].contains("duration_ms=123.5"));
        assert!(events[0].contains("operation_id=\"operation-42\""));
        assert!(events[0].contains("process_instance_id=\"process-42\""));
        assert!(events[1].contains(" INFO bcs_http_access: http.request.response_ready"));
        assert!(events[1].contains("status=200"));
    }

}
