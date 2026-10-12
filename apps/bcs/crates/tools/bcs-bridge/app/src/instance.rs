//! One local bridge per user, shared by foreground and background commands.
//! PID is diagnostic only: control requests target a private socket and a fresh
//! instance ID, so a stale/reused PID can never cause another process to be killed.
use std::{fs::{self, File, OpenOptions}, io::{Read, Write}, os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf}, process::{Child, Command, Stdio}, sync::{Arc, atomic::{AtomicU8, Ordering}},
    time::{Duration, SystemTime, UNIX_EPOCH}};

use anyhow::{Context, bail};
use bcs_bridge_core::{config::ProviderConfig, engine::EngineRegistry};
use serde::{Deserialize, Serialize};
use tokio::{io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader}, net::{UnixListener, UnixStream},
    sync::oneshot, time::{Instant, sleep, timeout}};
use tokio_util::sync::CancellationToken;

const STARTING: u8 = 0;
const RUNNING: u8 = 1;
const STOPPING: u8 = 2;
const CONTROL_TIMEOUT: Duration = Duration::from_secs(2);
const LIFECYCLE_TIMEOUT: Duration = Duration::from_secs(10);

/// Runtime files of one bridge instance, all under `directory` (by default
/// `~/.bcn-bridge`). Separate directories hold independent instances.
struct Paths { directory: PathBuf }
impl Paths {
    fn new(directory: &Path) -> Self { Self { directory: directory.to_owned() } }
    fn file(&self, name: &str) -> PathBuf { self.directory.join(name) }
    fn create(&self) -> anyhow::Result<()> {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.directory)
            .context("Cannot create bridge runtime directory")
    }
    fn lock(&self, create: bool) -> anyhow::Result<Option<File>> {
        let file = private_file(&self.file("bridge.lock"), create, false)?;
        match file.try_lock() {
            Ok(()) => Ok(Some(file)),
            Err(fs::TryLockError::WouldBlock) => Ok(None),
            Err(error) => Err(error).context("Cannot lock bridge runtime file"),
        }
    }
    fn is_locked(&self) -> anyhow::Result<bool> {
        // Do not create files just to query an instance that has never run.
        if !self.file("bridge.lock").try_exists().context("Cannot inspect bridge runtime lock")? { return Ok(false); }
        Ok(self.lock(false)?.is_none())
    }
    fn record(&self) -> anyhow::Result<Record> {
        let file = File::open(self.file("bridge.pid.json")).context("Cannot read bridge instance record")?;
        serde_json::from_reader(Read::take(file, 8192)).context("Invalid bridge instance record")
    }
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
struct Request { instance_id: String, command: String }

#[derive(Deserialize, Serialize)]
struct Response { record: Record, state: String }

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

pub async fn run(config: ProviderConfig, engines: &EngineRegistry, config_path: &Path, runtime_dir: &Path) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    paths.create()?;
    let lock = paths.lock(true)?.context("A bridge instance is already starting or running; use bcs-bridge status or stop")?;
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
    let mut file = tempfile::NamedTempFile::new_in(&instance.paths.directory)?;
    file.write_all(&serde_json::to_vec(&record)?)?;
    file.persist(instance.paths.file("bridge.pid.json")).context("Cannot save bridge instance record")?;
    tracing_subscriber::fmt().with_ansi(false)
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"))).try_init()
        .map_err(|_| anyhow::anyhow!("Cannot initialize bridge logging"))?;
    let phase = Arc::new(AtomicU8::new(STARTING));
    let shutdown = CancellationToken::new();
    let control = tokio::spawn(control_loop(listener, record, phase.clone(), shutdown.clone()));
    let (ready, initialized) = oneshot::channel();
    let service = bcs_bridge_core::service::serve_managed(config, engines, shutdown, Some(ready));
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

async fn control_loop(listener: UnixListener, record: Record, phase: Arc<AtomicU8>, shutdown: CancellationToken) {
    loop {
        let Ok((stream, _)) = listener.accept().await else { break; };
        // Bound each local request, including clients that connect but never send a line.
        let _ = timeout(CONTROL_TIMEOUT, handle_control(stream, &record, &phase, &shutdown)).await;
    }
}

async fn handle_control(stream: UnixStream, record: &Record, phase: &AtomicU8, shutdown: &CancellationToken) -> anyhow::Result<()> {
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
    let response = Response { record: record.clone(), state: state.into() };
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
        let mut reader = BufReader::new(stream.take(8192));
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

pub async fn status(runtime_dir: &Path) -> anyhow::Result<()> {
    match inspect(&Paths::new(runtime_dir)).await? {
        None => println!("Bridge is stopped"),
        Some(response) => println!("Bridge is {} (PID {}, config {})", response.state, response.record.pid, response.record.config.display()),
    }
    Ok(())
}

pub async fn stop(runtime_dir: &Path) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    let Some(response) = inspect(&paths).await? else {
        println!("Bridge is stopped");
        return Ok(());
    };
    request(&paths, &response.record, "stop").await.context("Cannot request shutdown of the verified bridge instance")?;
    let deadline = Instant::now() + LIFECYCLE_TIMEOUT;
    loop {
        if !paths.is_locked()? || paths.record().is_ok_and(|record| record.instance_id != response.record.instance_id) {
            println!("Bridge stopped (PID {})", response.record.pid);
            return Ok(());
        }
        if Instant::now() >= deadline { bail!("Bridge shutdown timed out; the instance is still stopping. Check bridge.log"); }
        sleep(Duration::from_millis(50)).await;
    }
}

// A failed/interrupted launch must not leave a newly spawned service behind.
struct PendingChild(Option<Child>);
impl Drop for PendingChild {
    fn drop(&mut self) {
        if let Some(child) = &mut self.0 { let _ = child.kill(); let _ = child.wait(); }
    }
}

pub async fn start_daemon(config_path: &Path, runtime_dir: &Path) -> anyhow::Result<()> {
    let paths = Paths::new(runtime_dir);
    if paths.is_locked()? { bail!("A bridge instance is already starting or running; use bcs-bridge status or stop"); }
    paths.create()?;
    let log = private_file(&paths.file("bridge.log"), true, true)?;
    let child = Command::new(std::env::current_exe().context("Cannot locate the bridge executable")?)
        .args(["start", "--daemon-child", "--config"]).arg(config_path)
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
                pending.0.take();
                println!("Bridge started in background (PID {pid}). Log: {}", paths.file("bridge.log").display());
                return Ok(());
            }
        }
        if Instant::now() >= deadline { bail!("Background bridge startup timed out. See {}", paths.file("bridge.log").display()); }
        sleep(Duration::from_millis(50)).await;
    }
}
