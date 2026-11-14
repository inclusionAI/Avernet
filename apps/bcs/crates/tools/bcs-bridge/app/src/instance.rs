//! One local bridge per user, shared by foreground and background commands.
//! PID is diagnostic only: control requests target a private socket and a fresh
//! instance ID, so a stale/reused PID can never cause another process to be killed.
use std::{fs::{self, File, OpenOptions}, io::{Read, Write}, os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf}, process::{Child, Command, Stdio}, sync::{Arc, atomic::{AtomicU8, Ordering}},
    time::{Duration, SystemTime, UNIX_EPOCH}};

use anyhow::{Context, bail};
use serde::{Deserialize, Serialize};
use tokio::{io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader}, net::{UnixListener, UnixStream},
    sync::oneshot, time::{Instant, sleep, timeout}};
use tokio_util::sync::CancellationToken;

const STARTING: u8 = 0;
const RUNNING: u8 = 1;
const STOPPING: u8 = 2;
const CONTROL_TIMEOUT: Duration = Duration::from_secs(2);
const LIFECYCLE_TIMEOUT: Duration = Duration::from_secs(10);
const SUPERVISOR_POLL: Duration = Duration::from_millis(200);
const SUPERVISOR_STOP_TIMEOUT: Duration = Duration::from_secs(20);
const RESTART_DELAY: Duration = Duration::from_secs(5);
const MAX_RESTART_DELAY: Duration = Duration::from_secs(60);

/// Runtime files of one bridge instance, all under `directory` (by default
/// `~/.bcn-bridge`). Separate directories hold independent instances.
#[derive(Clone)]
struct Paths { directory: PathBuf }
pub(crate) fn registration_guard(directory: &Path) -> anyhow::Result<File> {
    let paths = Paths::new(directory); paths.create()?;
    if paths.supervisor_is_locked()? { bail!("Stop the bridge auto-restart supervisor before adding a Bot"); }
    paths.lock(true)?.context("Stop the bridge before adding a Bot")
}
impl Paths {
    fn new(directory: &Path) -> Self { Self { directory: directory.to_owned() } }
    fn file(&self, name: &str) -> PathBuf { self.directory.join(name) }
    fn create(&self) -> anyhow::Result<()> {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.directory)
            .context("Cannot create bridge runtime directory")
    }
    fn named_lock(&self, name: &str, create: bool) -> anyhow::Result<Option<File>> {
        let file = private_file(&self.file(name), create, false)?;
        match file.try_lock() {
            Ok(()) => Ok(Some(file)),
            Err(fs::TryLockError::WouldBlock) => Ok(None),
            Err(error) => Err(error).context("Cannot lock bridge runtime file"),
        }
    }
    fn lock(&self, create: bool) -> anyhow::Result<Option<File>> { self.named_lock("bridge.lock", create) }
    fn supervisor_lock(&self, create: bool) -> anyhow::Result<Option<File>> { self.named_lock("bridge-supervisor.lock", create) }
    fn named_is_locked(&self, name: &str) -> anyhow::Result<bool> {
        // Do not create files just to query an instance that has never run.
        if !self.file(name).try_exists().context("Cannot inspect bridge runtime lock")? { return Ok(false); }
        Ok(self.named_lock(name, false)?.is_none())
    }
    fn is_locked(&self) -> anyhow::Result<bool> { self.named_is_locked("bridge.lock") }
    fn supervisor_is_locked(&self) -> anyhow::Result<bool> { self.named_is_locked("bridge-supervisor.lock") }
    fn named_record(&self, name: &str) -> anyhow::Result<Record> {
        let file = File::open(self.file(name)).context("Cannot read bridge instance record")?;
        serde_json::from_reader(Read::take(file, 8192)).context("Invalid bridge instance record")
    }
    fn record(&self) -> anyhow::Result<Record> { self.named_record("bridge.pid.json") }
    fn supervisor_record(&self) -> anyhow::Result<Record> { self.named_record("bridge-supervisor.pid.json") }
}

fn private_file(path: &Path, create: bool, append: bool) -> anyhow::Result<File> {
    let file = OpenOptions::new().read(true).write(true).create(create).append(append).mode(0o600)
        .custom_flags(rustix::fs::OFlags::NOFOLLOW.bits() as i32).open(path)
        .context("Cannot open private bridge runtime file")?;
    if !file.metadata()?.is_file() { bail!("Bridge runtime file must be a regular file"); }
    if create { file.set_permissions(fs::Permissions::from_mode(0o600))?; }
    Ok(file)
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
struct Record {
    pid: u32,
    instance_id: String,
    config: PathBuf,
    started_at: u64,
}

#[derive(Deserialize, Serialize)]
struct SupervisorStop { instance_id: String }

fn write_record(paths: &Paths, name: &str, record: &Record) -> anyhow::Result<()> {
    let mut file = tempfile::NamedTempFile::new_in(&paths.directory)?;
    file.write_all(&serde_json::to_vec(record)?)?;
    file.persist(paths.file(name)).context("Cannot save bridge instance record")?;
    Ok(())
}

fn active_supervisor(paths: &Paths) -> anyhow::Result<Option<Record>> {
    if !paths.supervisor_is_locked()? { return Ok(None); }
    match paths.supervisor_record() {
        Ok(record) => Ok(Some(record)),
        Err(_) if !paths.supervisor_is_locked()? => Ok(None),
        Err(error) => Err(error).context("Cannot verify the bridge auto-restart supervisor"),
    }
}

#[derive(Deserialize, Serialize)]
struct Request { instance_id: String, command: String }

#[derive(Deserialize, Serialize)]
struct Response {
    record: Record,
    state: String,
    #[serde(default)]
    health: String,
    #[serde(default)]
    bots: Vec<bcs_bridge_core::status::BotStatus>,
}

struct Instance { paths: Paths, _lock: File }
impl Drop for Instance {
    fn drop(&mut self) {
        let _ = fs::remove_file(self.paths.file("bridge.sock"));
        let _ = fs::remove_file(self.paths.file("bridge.pid.json"));
        // Keep the inode: unlinking a lock lets concurrent starts lock different files.
        // The File closes last, after cleanup. Rust opens it close-on-exec so engine
        // children cannot retain the lock after the bridge exits.
    }
}

struct SupervisorInstance { paths: Paths, _lock: File, record: Record }
impl SupervisorInstance {
    fn acquire(paths: &Paths, config_path: &Path) -> anyhow::Result<Self> {
        paths.create()?;
        let lock = paths.supervisor_lock(true)?.context("A bridge auto-restart supervisor is already running")?;
        let record = Record { pid: std::process::id(), instance_id: uuid::Uuid::new_v4().to_string(),
            config: config_path.into(), started_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs() };
        // Remove stale stops before publishing the record that lets stop target this instance.
        // A stop record is scoped to a random supervisor ID; an old record cannot stop this instance.
        let _ = fs::remove_file(paths.file("bridge-supervisor.stop"));
        write_record(paths, "bridge-supervisor.pid.json", &record)?;
        Ok(Self { paths: paths.clone(), _lock: lock, record })
    }
    fn stop_requested(&self) -> anyhow::Result<bool> {
        let path = self.paths.file("bridge-supervisor.stop");
        if !path.try_exists().context("Cannot inspect supervisor stop request")? { return Ok(false); }
        let file = File::open(path).context("Cannot read supervisor stop request")?;
        let request: SupervisorStop = serde_json::from_reader(Read::take(file, 8192)).context("Invalid supervisor stop request")?;
        Ok(request.instance_id == self.record.instance_id)
    }
}
impl Drop for SupervisorInstance {
    fn drop(&mut self) {
        let _ = fs::remove_file(self.paths.file("bridge-supervisor.pid.json"));
        let _ = fs::remove_file(self.paths.file("bridge-supervisor.stop"));
        // As with the worker lock, retain the inode and release the File last.
    }
}

/// Fence runtime startup throughout replacement registration, without stopping an instance.
pub fn reserve_registration(runtime_dir: &Path) -> anyhow::Result<File> {
    let paths = Paths::new(runtime_dir);
    paths.create()?;
    if paths.supervisor_is_locked()? { bail!("Stop the bridge auto-restart supervisor before changing registered configuration"); }
    paths.lock(true)?.context("A bridge instance is running; stop it with bcs-bridge stop before changing registered configuration")
}

pub async fn run(app: &crate::app::BridgeApp, config_path: &Path, runtime_dir: &Path, cwd_override: Option<&Path>) -> anyhow::Result<()> {
    // Invalid configuration must not create lifecycle files. Fence the exact
    // validated snapshot after acquiring the registration/startup lock.
    let original = fs::read(config_path).context("Cannot read bridge configuration")?;
    let config = crate::cli::load_start(config_path, app, cwd_override).await?;
    let paths = Paths::new(runtime_dir);
    paths.create()?;
    let lock = paths.lock(true)?.context("A bridge instance is already starting or running; use bcs-bridge status or stop")?;
    if fs::read(config_path)? != original { bail!("Configuration changed during startup validation; retry"); }
    let instance = Instance { paths, _lock: lock };
    let socket = instance.paths.file("bridge.sock");
    match fs::remove_file(&socket) {
        Ok(()) => {},
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
        Err(error) => return Err(error).context("Cannot remove stale bridge control socket"),
    }
    let listener = UnixListener::bind(&socket).context("Cannot bind bridge control socket")?;
    fs::set_permissions(&socket, fs::Permissions::from_mode(0o600))?;
    let record = Record { pid: std::process::id(), instance_id: uuid::Uuid::new_v4().to_string(),
        config: config_path.into(), started_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs() };
    write_record(&instance.paths, "bridge.pid.json", &record)?;
    tracing_subscriber::fmt().with_ansi(false)
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"))).try_init()
        .map_err(|_| anyhow::anyhow!("Cannot initialize bridge logging"))?;
    let phase = Arc::new(AtomicU8::new(STARTING));
    let shutdown = CancellationToken::new();
    let observations = bcs_bridge_core::status::BridgeStatus::default();
    let control = tokio::spawn(control_loop(listener, record, phase.clone(), shutdown.clone(), observations.clone()));
    let (ready, initialized) = oneshot::channel();
    let service = bcs_bridge_core::service::serve_bridge_managed(config, app.engines(), shutdown, Some(ready), observations);
    tokio::pin!(service);
    let result = tokio::select! {
        biased;
        result = &mut service => result,
        ready = initialized => {
            if ready.is_ok() {
                let _ = phase.compare_exchange(STARTING, RUNNING, Ordering::SeqCst, Ordering::SeqCst);
            }
            service.await
        }
    };
    control.abort();
    let _ = control.await;
    result
}

async fn control_loop(listener: UnixListener, record: Record, phase: Arc<AtomicU8>, shutdown: CancellationToken, observations: bcs_bridge_core::status::BridgeStatus) {
    loop {
        let Ok((stream, _)) = listener.accept().await else { break; };
        // Bound each local request, including clients that connect but never send a line.
        let _ = timeout(CONTROL_TIMEOUT, handle_control(stream, &record, &phase, &shutdown, &observations)).await;
    }
}

async fn handle_control(stream: UnixStream, record: &Record, phase: &AtomicU8, shutdown: &CancellationToken, observations: &bcs_bridge_core::status::BridgeStatus) -> anyhow::Result<()> {
    let mut reader = BufReader::new(stream.take(4096));
    let mut line = String::new();
    reader.read_line(&mut line).await?;
    let request: Request = serde_json::from_str(&line)?;
    if request.instance_id != record.instance_id { bail!("Bridge instance changed"); }
    match request.command.as_str() {
        "status" => {},
        "stop" => { phase.store(STOPPING, Ordering::SeqCst); },
        _ => bail!("Unknown bridge control command"),
    }
    let state = match phase.load(Ordering::SeqCst) { STARTING => "starting", RUNNING => "running", _ => "stopping" };
    let bots = observations.snapshot();
    let health = if bots.iter().any(|bot| matches!(bot.state.as_str(), "failed" | "offline")) { "degraded" }
        else if !bots.is_empty() && bots.iter().all(|bot| matches!(bot.state.as_str(), "online" | "listening")) { "ready" }
        else { "connecting" };
    let response = Response { record: record.clone(), state: state.into(), health: health.into(), bots };
    let mut bytes = serde_json::to_vec(&response)?;
    bytes.push(b'\n');
    let result = reader.get_mut().get_mut().write_all(&bytes).await;
    if request.command == "stop" { shutdown.cancel(); }
    result.context("Cannot reply to bridge control request")
}

async fn request(paths: &Paths, record: &Record, command: &str) -> anyhow::Result<Response> {
    timeout(CONTROL_TIMEOUT, async {
        let mut stream = UnixStream::connect(paths.file("bridge.sock")).await?;
        let mut bytes = serde_json::to_vec(&Request { instance_id: record.instance_id.clone(), command: command.into() })?;
        bytes.push(b'\n');
        stream.write_all(&bytes).await?;
        let mut reader = BufReader::new(stream.take(512 * 1024));
        let mut line = String::new();
        reader.read_line(&mut line).await?;
        let response: Response = serde_json::from_str(&line)?;
        if response.record != *record { bail!("Bridge process identity does not match its instance record"); }
        Ok(response)
    }).await.context("Bridge control request timed out")?
}

async fn inspect(paths: &Paths) -> anyhow::Result<Option<Response>> {
    if !paths.is_locked()? { return Ok(None); }
    let result = match paths.record() {
        Ok(record) => request(paths, &record, "status").await,
        Err(error) => Err(error),
    };
    match result {
        Ok(response) => Ok(Some(response)),
        Err(_) if !paths.is_locked()? => Ok(None),
        Err(_) => bail!("Bridge status unknown: runtime lock is held but the instance cannot be verified; check bridge.log"),
    }
}

pub async fn current_configuration(runtime_dir: &Path) -> anyhow::Result<Option<PathBuf>> {
    let paths = Paths::new(runtime_dir);
    if let Some(response) = inspect(&paths).await? { return Ok(Some(response.record.config)); }
    Ok(active_supervisor(&paths)?.map(|record| record.config))
}

pub async fn status(runtime_dir: &Path, json: bool) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    let supervisor = active_supervisor(&paths)?;
    match inspect(&paths).await? {
        None if json => {
            let mut value = serde_json::json!({"state":"stopped","health":"stopped","bots":[]});
            if let Some(record) = supervisor {
                value["state"] = "restarting".into();
                value["auto_restart"] = true.into();
                value["supervisor_pid"] = record.pid.into();
                value["config"] = record.config.display().to_string().into();
            }
            println!("{value}");
        }
        None => {
            if let Some(record) = supervisor {
                println!("Bridge is restarting (supervisor PID {}, config {})", record.pid, record.config.display());
                println!("Auto restart: enabled");
            } else {
                println!("Bridge is stopped");
            }
        }
        Some(response) if json => {
            let mut value = serde_json::to_value(response)?;
            if let Some(record) = supervisor {
                value["auto_restart"] = true.into();
                value["supervisor_pid"] = record.pid.into();
            }
            println!("{}", serde_json::to_string(&value)?);
        }
        Some(response) => {
            println!("Bridge is {} (PID {}, config {})", response.state, response.record.pid, response.record.config.display());
            println!("Active profile: {}", crate::profiles::active_name(runtime_dir, &response.record.config).unwrap_or("legacy/custom"));
            println!("Connections: {}", response.health);
            if let Some(record) = supervisor { println!("Auto restart: enabled (supervisor PID {})", record.pid); }
            for bot in response.bots {
                println!("  {} / {}: {} ({})", bot.provider_id, bot.bot_id.as_deref().unwrap_or(&bot.provider_bot_ref), bot.state, bot.engine);
            }
        }
    }
    Ok(())
}

fn request_supervisor_stop(paths: &Paths, record: &Record) -> anyhow::Result<()> {
    let request = SupervisorStop { instance_id: record.instance_id.clone() };
    let mut file = tempfile::NamedTempFile::new_in(&paths.directory)?;
    file.write_all(&serde_json::to_vec(&request)?)?;
    file.persist(paths.file("bridge-supervisor.stop")).context("Cannot save supervisor stop request")?;
    Ok(())
}

async fn stop_worker(paths: &Paths) -> anyhow::Result<Option<Response>> {
    let Some(response) = inspect(paths).await? else { return Ok(None); };
    request(paths, &response.record, "stop").await.context("Cannot request shutdown of the verified bridge instance")?;
    let deadline = Instant::now() + LIFECYCLE_TIMEOUT;
    loop {
        if !paths.is_locked()? || paths.record().is_ok_and(|record| record.instance_id != response.record.instance_id) {
            return Ok(Some(response));
        }
        if Instant::now() >= deadline { bail!("Bridge shutdown timed out; the instance is still stopping. Check bridge.log"); }
        sleep(Duration::from_millis(50)).await;
    }
}

async fn stop_supervised_worker(paths: &Paths) -> anyhow::Result<()> {
    let deadline = Instant::now() + LIFECYCLE_TIMEOUT;
    loop {
        match stop_worker(paths).await {
            Ok(_) => return Ok(()),
            Err(error) if Instant::now() < deadline => {
                eprintln!("bcs-bridge supervisor: waiting to stop worker: {error}");
                sleep(Duration::from_millis(100)).await;
            }
            Err(error) => return Err(error),
        }
    }
}

pub async fn stop(runtime_dir: &Path) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    if let Some(record) = active_supervisor(&paths)? {
        // The supervisor owns the complete stop sequence, including a worker that is still starting.
        request_supervisor_stop(&paths, &record)?;
        let deadline = Instant::now() + SUPERVISOR_STOP_TIMEOUT;
        while paths.supervisor_is_locked()? {
            if Instant::now() >= deadline { bail!("Bridge supervisor shutdown timed out. Check bridge.log"); }
            sleep(Duration::from_millis(50)).await;
        }
        if paths.is_locked()? { bail!("Bridge supervisor stopped but the worker is still running. Check bridge.log"); }
        println!("Bridge stopped; auto-restart supervisor stopped (PID {})", record.pid);
        return Ok(());
    }
    if let Some(worker) = stop_worker(&paths).await? {
        println!("Bridge stopped (PID {})", worker.record.pid);
    } else {
        println!("Bridge is stopped");
    }
    Ok(())
}

// A failed/interrupted launch must not leave a newly spawned service behind.
struct PendingChild(Option<Child>);
impl Drop for PendingChild {
    fn drop(&mut self) {
        if let Some(child) = &mut self.0 { let _ = child.kill(); let _ = child.wait(); }
    }
}

async fn start_worker(config_path: &Path, runtime_dir: &Path, cwd: Option<&Path>) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    if paths.is_locked()? { bail!("A bridge instance is already starting or running; use bcs-bridge status or stop"); }
    paths.create()?;
    let log = private_file(&paths.file("bridge.log"), true, true)?;
    let mut command = Command::new(std::env::current_exe().context("Cannot locate the bridge executable")?);
    command.args(["start", "--daemon-child", "--config"]).arg(config_path);
    if let Some(cwd) = cwd { command.arg("--cwd").arg(cwd.canonicalize().context("Cannot resolve working directory")?); }
    let child = command
        .stdin(Stdio::null()).stdout(log.try_clone()?).stderr(log).spawn()
        .context("Cannot launch background bridge")?;
    let pid = child.id();
    let mut pending = PendingChild(Some(child));
    let deadline = Instant::now() + LIFECYCLE_TIMEOUT;
    loop {
        if let Some(child) = &mut pending.0 {
            if let Some(status) = child.try_wait()? {
                bail!("Background bridge exited before becoming ready ({status}). See {}", paths.file("bridge.log").display());
            }
        }
        if let Ok(Some(response)) = inspect(&paths).await {
            if response.record.pid == pid && response.state == "running" {
                if let Some(mut child) = pending.0.take() {
                    // A long-lived supervisor remains the worker's parent; reap each exit so crashes do not accumulate zombies.
                    let _ = std::thread::spawn(move || { let _ = child.wait(); });
                }
                println!("Bridge started in background (PID {pid}). Log: {}", paths.file("bridge.log").display());
                println!("Process initialized; use bcs-bridge status to check Bot connectivity.");
                return Ok(());
            }
        }
        if Instant::now() >= deadline { bail!("Background bridge startup timed out. See {}", paths.file("bridge.log").display()); }
        sleep(Duration::from_millis(50)).await;
    }
}

async fn start_supervisor(config_path: &Path, runtime_dir: &Path, cwd: Option<&Path>) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    let log = private_file(&paths.file("bridge.log"), true, true)?;
    let mut command = Command::new(std::env::current_exe().context("Cannot locate the bridge executable")?);
    command.args(["start", "--daemon-supervisor", "--config"]).arg(config_path);
    if let Some(cwd) = cwd { command.arg("--cwd").arg(cwd.canonicalize().context("Cannot resolve working directory")?); }
    let child = command
        .stdin(Stdio::null()).stdout(log.try_clone()?).stderr(log).spawn()
        .context("Cannot launch bridge auto-restart supervisor")?;
    let pid = child.id();
    let mut pending = PendingChild(Some(child));
    let deadline = Instant::now() + LIFECYCLE_TIMEOUT;
    loop {
        if let Some(child) = &mut pending.0 {
            if let Some(status) = child.try_wait()? {
                bail!("Bridge auto-restart supervisor exited before becoming ready ({status}). See {}", paths.file("bridge.log").display());
            }
        }
        if let Ok(Some(record)) = active_supervisor(&paths) {
            if record.pid == pid {
                pending.0.take();
                println!("Auto restart: enabled (supervisor PID {pid})");
                return Ok(());
            }
        }
        if Instant::now() >= deadline { bail!("Bridge auto-restart supervisor startup timed out. See {}", paths.file("bridge.log").display()); }
        sleep(Duration::from_millis(50)).await;
    }
}

pub async fn start_daemon(config_path: &Path, runtime_dir: &Path, cwd: Option<&Path>, auto_restart: bool) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    if paths.supervisor_is_locked()? { bail!("A bridge auto-restart supervisor is already running; use bcs-bridge status or stop"); }
    start_worker(config_path, runtime_dir, cwd).await?;
    if !auto_restart { return Ok(()); }
    if let Err(error) = start_supervisor(config_path, runtime_dir, cwd).await {
        let _ = stop_worker(&paths).await;
        return Err(error);
    }
    Ok(())
}

fn restart_delay(failures: u32) -> Duration {
    let multiplier = 1u64 << failures.min(4);
    RESTART_DELAY.saturating_mul(multiplier as u32).min(MAX_RESTART_DELAY)
}

async fn supervisor_wait(supervisor: &SupervisorInstance, duration: Duration) -> anyhow::Result<bool> {
    let deadline = Instant::now() + duration;
    loop {
        if supervisor.stop_requested()? { return Ok(true); }
        let now = Instant::now();
        if now >= deadline { return Ok(false); }
        sleep((deadline - now).min(Duration::from_millis(100))).await;
    }
}

pub async fn run_supervisor(config_path: &Path, runtime_dir: &Path, cwd: Option<&Path>) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    let supervisor = SupervisorInstance::acquire(&paths, config_path)?;
    let mut observed_worker = None::<String>;
    let mut failures = 0u32;
    loop {
        if supervisor.stop_requested()? { break; }
        match inspect(&paths).await {
            Ok(Some(response)) => {
                if observed_worker.as_deref() != Some(response.record.instance_id.as_str()) {
                    eprintln!("bcs-bridge supervisor: worker is running (PID {})", response.record.pid);
                    observed_worker = Some(response.record.instance_id);
                }
                failures = 0;
                if supervisor_wait(&supervisor, SUPERVISOR_POLL).await? { break; }
            }
            Ok(None) => {
                if let Some(instance_id) = observed_worker.take() {
                    eprintln!("bcs-bridge supervisor: worker {instance_id} exited; scheduling a restart");
                }
                let delay = restart_delay(failures);
                eprintln!("bcs-bridge supervisor: restarting worker in {} second(s)", delay.as_secs());
                if supervisor_wait(&supervisor, delay).await? { break; }
                match start_worker(config_path, runtime_dir, cwd).await {
                    Ok(()) => failures = 0,
                    Err(error) => {
                        failures = failures.saturating_add(1);
                        eprintln!("bcs-bridge supervisor: worker restart failed: {error}");
                    }
                }
            }
            Err(error) => {
                // A locked but unverifiable worker may still be starting or stopping; never duplicate it.
                eprintln!("bcs-bridge supervisor: worker state is unavailable: {error}");
                if supervisor_wait(&supervisor, Duration::from_secs(1)).await? { break; }
            }
        }
    }
    if supervisor.stop_requested()? { stop_supervised_worker(&paths).await?; }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_restart_backoff_is_bounded() {
        for (failures, seconds) in [(0, 5), (1, 10), (2, 20), (3, 40), (4, 60), (5, 60), (u32::MAX, 60)] {
            assert_eq!(restart_delay(failures), Duration::from_secs(seconds));
        }
    }

    #[test]
    fn supervisor_stop_is_scoped_to_the_current_instance() {
        let directory = tempfile::tempdir().unwrap();
        let paths = Paths::new(directory.path());
        paths.create().unwrap();
        fs::write(paths.file("bridge-supervisor.stop"), serde_json::to_vec(&SupervisorStop { instance_id: "stale".into() }).unwrap()).unwrap();
        let supervisor = SupervisorInstance::acquire(&paths, Path::new("/tmp/bridge.toml")).unwrap();
        assert!(!supervisor.stop_requested().unwrap());
        request_supervisor_stop(&paths, &supervisor.record).unwrap();
        assert!(supervisor.stop_requested().unwrap());
    }
}
