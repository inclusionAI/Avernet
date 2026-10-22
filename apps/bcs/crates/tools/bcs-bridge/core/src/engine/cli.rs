use std::path::Path;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};

use super::trace::TraceContext;

pub struct CliSession {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<tokio::process::ChildStdout>>,
    stdout_drain: Option<tokio::task::JoinHandle<()>>,
    trace: Option<TraceContext>,
    /// Set once the child has been reaped (`wait` returned). Later Drops must
    /// not signal the process group: the reaped pid is recyclable and
    /// `kill(-pgid)` could then hit an unrelated process group. While the
    /// child is unreaped (even as a zombie) the kernel cannot recycle its
    /// pid, so the group address still belongs to this session's tree only.
    reaped: bool,
    stdout_buffer: Vec<u8>,
    stdout_limit: usize,
}

impl CliSession {
    pub async fn spawn(
        bin: &Path,
        args: &[String],
        cwd: &Path,
        env: &[(String, String)],
        trace: Option<TraceContext>,
    ) -> std::io::Result<Self> {
        Self::spawn_inner(bin, args, cwd, env, trace, true).await
    }

    #[cfg(unix)]
    pub(super) async fn spawn_for_socket(
        bin: &Path, args: &[String], cwd: &Path, env: &[(String, String)], trace: Option<TraceContext>,
    ) -> std::io::Result<Self> {
        Self::spawn_inner(bin, args, cwd, env, trace, false).await
    }

    async fn spawn_inner(
        bin: &Path, args: &[String], cwd: &Path, env: &[(String, String)],
        trace: Option<TraceContext>, rpc_stdout: bool,
    ) -> std::io::Result<Self> {
        let mut cmd = Command::new(bin);
        cmd.args(args)
            .current_dir(cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        // Engine CLIs are wrapper runtimes (e.g. cfuse → node agent → claude →
        // MCP servers): the spawned process is only the top of a tree. Make it
        // a process-group leader so teardown can SIGKILL the whole tree at
        // once; descendants that do not `setsid` (deliberately detached
        // daemons) inherit the group and die with it.
        #[cfg(unix)]
        {
            cmd.process_group(0);
        }
        for (k, v) in env {
            cmd.env(k, v);
        }
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io_err("stdin not piped"))?;
        let stdout = child.stdout.take().ok_or_else(|| io_err("stdout not piped"))?;
        #[cfg(target_os = "linux")]
        if rpc_stdout { enlarge_stdout_pipe(&stdout); }
        let mut stderr = child
            .stderr
            .take()
            .ok_or_else(|| io_err("stderr not piped"))?;
        let stderr_trace = trace.clone();
        tokio::spawn(async move {
            let mut reader = BufReader::new(&mut stderr);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        let line = line.trim_end();
                        if let Some(trace) = &stderr_trace {
                            trace.record_stderr(line);
                        }
                        tracing::debug!(
                            target: "bcs_bridge_core::engine",
                            stderr = super::trace::strip_ansi(line)
                        );
                    }
                }
            }
        });
        let (stdout, stdout_drain) = if rpc_stdout {
            (Some(BufReader::new(stdout)), None)
        } else {
            // Socket RPC never reads stdout. Drain arbitrary diagnostics as
            // bounded byte chunks, without retaining or parsing them as JSONL.
            let task = tokio::spawn(async move {
                let mut stdout = stdout;
                let _ = tokio::io::copy(&mut stdout, &mut tokio::io::sink()).await;
            });
            (None, Some(task))
        };
        Ok(Self {
            child,
            stdin: Some(stdin),
            stdout,
            stdout_drain,
            trace,
            reaped: false,
            stdout_buffer: Vec::new(),
            stdout_limit: usize::MAX,
        })
    }

    /// A multiplexed process traces stdout after assigning messages to runs.
    pub(super) fn disable_raw_trace(&mut self) { self.trace = None; }

    pub async fn write_line(&mut self, line: &str) -> std::io::Result<()> {
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| io_err("stdin already closed"))?;
        stdin.write_all(line.as_bytes()).await?;
        stdin.write_all(b"\n").await?;
        stdin.flush().await
    }

    /// Close the child's stdin pipe by dropping the handle.
    ///
    /// `codex exec` reads a piped stdin as additional input; when the prompt is
    /// passed as an argv positional (the codex path), stdin must be closed
    /// immediately after spawn so the child sees EOF and proceeds with the argv
    /// prompt only — instead of blocking on, or consuming, stdin.
    pub fn close_stdin(&mut self) {
        self.stdin = None;
    }

    pub(super) fn limit_stdout_line(&mut self, bytes: usize) { self.stdout_limit = bytes; }

    pub async fn next_line(&mut self) -> std::io::Result<Option<String>> {
        let stdout = self.stdout.as_mut().ok_or_else(|| io_err("stdout is diagnostic-only"))?;
        let n = loop {
            let available = stdout.fill_buf().await?;
            if available.is_empty() { break 0; }
            let end = available.iter().position(|b| *b == b'\n');
            let take = end.map_or(available.len(), |i| i + 1);
            if take > self.stdout_limit.saturating_sub(self.stdout_buffer.len()) {
                self.stdout_buffer.clear();
                return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "engine stdout line exceeds configured limit"));
            }
            self.stdout_buffer.extend_from_slice(&available[..take]);
            stdout.consume(take);
            if end.is_some() { break take; }
        };
        if n == 0 && self.stdout_buffer.is_empty() {
            return Ok(None);
        }
        let line = String::from_utf8(std::mem::take(&mut self.stdout_buffer))
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
        if let Some(trace) = &self.trace {
            trace.record_raw(line.trim_end_matches(['\n', '\r']));
        }
        Ok(Some(line.trim_end_matches(['\n', '\r']).to_string()))
    }

    /// SIGKILL the engine's whole process group. The spawned engine leads its
    /// own group (`process_group(0)` at spawn), and the group only ever
    /// contains this session's tree, so the negative-pid signal cannot reach
    /// unrelated processes. Falls back to killing the direct child alone when
    /// group signaling fails (e.g. non-unix, or the group is already gone).
    ///
    /// Callers that can `.await` should prefer `graceful_kill_process_group`,
    /// which SIGTERMs first and gives the CLI a bounded window to flush
    /// undelivered queue notifications before this hard kill.
    #[allow(unsafe_code)]
    fn kill_process_group(&mut self) {
        if self.reaped {
            return;
        }
        #[cfg(unix)]
        if let Some(pid) = self.child.id().filter(|pid| *pid > 0) {
            let pgid = pid as libc::pid_t;
            if unsafe { libc::kill(-pgid, libc::SIGKILL) } == 0 {
                return;
            }
        }
        if let Err(e) = self.child.start_kill() {
            tracing::debug!(target: "bcs_bridge_core::engine", "kill failed: {e}");
        }
    }

    /// Observe exit without reaping the group leader. Reaping first releases
    /// its PID and makes child.id() unavailable before the final group SIGKILL,
    /// leaving descendants that ignore SIGTERM alive.
    #[cfg(unix)]
    #[allow(unsafe_code)]
    fn leader_exited(&mut self) -> bool {
        let Some(pid) = self.child.id() else { return true; };
        let mut info = std::mem::MaybeUninit::<libc::siginfo_t>::zeroed();
        // SAFETY: info points to writable siginfo_t storage; this observes only
        // the child we own, and WNOWAIT preserves PID ownership until teardown.
        unsafe {
            libc::waitid(libc::P_PID, pid as libc::id_t, info.as_mut_ptr(),
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT) == 0
                && info.assume_init().si_pid() != 0
        }
    }

    #[cfg(not(unix))]
    fn leader_exited(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(Some(_)))
    }

    /// Graceful teardown: close stdin first, SIGTERM the whole process group,
    /// wait up to `GRACE_WINDOW` for the group to empty, only then SIGKILL.
    /// This lets the CLI flush undelivered task notifications out of its
    /// internal queue before dying — same-sequence kills without this window
    /// freeze those notifications in the transcript and crash resume-of-turn
    /// after resume (observed in production).
    #[allow(unsafe_code)]
    async fn graceful_kill_process_group(&mut self) {
        if self.reaped {
            return;
        }
        // 1. Close stdin — signals the CLI there will be no more input and it
        //    can begin its shutdown path.
        self.close_stdin();
        // 2. SIGTERM the group (catchable — the CLI can flush queues).
        #[cfg(unix)]
        if let Some(pid) = self.child.id().filter(|pid| *pid > 0) {
            let pgid = pid as libc::pid_t;
            let _ = unsafe { libc::kill(-pgid, libc::SIGTERM) };
        }
        // 3. Give the leader a bounded chance to exit without reaping it.
        //    Descendants may remain; the final group SIGKILL covers them.
        let deadline = std::time::Instant::now() + GRACE_WINDOW;
        while std::time::Instant::now() < deadline {
            if self.leader_exited() {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        // 4. Whatever's left gets SIGKILL.
        self.kill_process_group();
    }

    pub async fn kill(&mut self) {
        self.graceful_kill_process_group().await;
        let _ = self.child.wait().await;
        self.reaped = true;
        if let Some(task) = self.stdout_drain.take() { task.abort(); }
    }
}

impl Drop for CliSession {
    /// Teardown by drop is the bridge's Final path (run_turn returns on the
    /// engine's result line and drops the session), so it must reap the whole
    /// tree too — a single-child kill_on_drop orphans every descendant, which
    /// is how orphaned engine/claude/MCP trees accumulated until the host
    /// OOM'd. The graceful sequence runs synchronously here (can't `.await`):
    /// close stdin → SIGTERM group → short bounded spin → SIGKILL. The spin
    /// is proportional to what's still alive — a CLI that exits on SIGTERM
    /// in 50ms spends 50ms here, not the full 2 s.
    ///
    /// `kill_on_drop` stays enabled as a belt-and-braces fallback: it covers
    /// the direct child if anything in this synchronous path panics.
    #[allow(unsafe_code)]
    fn drop(&mut self) {
        if self.reaped {
            if let Some(task) = self.stdout_drain.take() { task.abort(); }
            return;
        }
        self.close_stdin();
        #[cfg(unix)]
        if let Some(pid) = self.child.id().filter(|pid| *pid > 0) {
            let pgid = pid as libc::pid_t;
            let _ = unsafe { libc::kill(-pgid, libc::SIGTERM) };
            let deadline = std::time::Instant::now() + GRACE_WINDOW;
            while std::time::Instant::now() < deadline {
                if self.leader_exited() {
                    break; // leader exited — SIGKILL below sweeps the rest.
                }
                std::thread::sleep(std::time::Duration::from_millis(25));
            }
        }
        self.kill_process_group();
        if let Some(task) = self.stdout_drain.take() { task.abort(); }
    }
}

/// Graceful-kill window: how long the CLI gets between SIGTERM and SIGKILL
/// to flush internal queues (undelivered task notifications) and exit.
/// Same order multica uses for its engine teardown.
const GRACE_WINDOW: std::time::Duration = std::time::Duration::from_secs(2);

/// Give the engine stdout pipe enough room for a complete JSONL event.
///
/// Some engines emit an assistant answer as one long JSONL line rather than as
/// smaller text deltas, and some builds write that line through a non-blocking
/// stdout descriptor. On hosts whose default pipe is only 4 KiB, a long answer
/// can therefore hit `EAGAIN` before the line reaches the bridge. Grow the pipe after spawn, before the engine starts producing
/// its final event. The requested size is best-effort because Linux may limit
/// it by the caller's pipe-page quota; smaller fallbacks still cover ordinary
/// long responses, while failure leaves the default pipe behavior unchanged.
#[cfg(target_os = "linux")]
#[allow(unsafe_code)]
fn enlarge_stdout_pipe(stdout: &tokio::process::ChildStdout) {
    use std::os::fd::AsRawFd;

    const REQUESTED_SIZES: [libc::c_int; 4] = [
        1024 * 1024,
        64 * 1024,
        16 * 1024,
        8 * 1024,
    ];
    let fd = stdout.as_raw_fd();
    for requested in REQUESTED_SIZES {
        // SAFETY: `fd` is borrowed from a live ChildStdout and F_SETPIPE_SZ
        // only adjusts the kernel pipe capacity associated with that fd.
        let actual = unsafe { libc::fcntl(fd, libc::F_SETPIPE_SZ, requested) };
        if actual >= 0 {
            tracing::debug!(
                target: "bcs_bridge_core::engine",
                requested,
                actual,
                "engine stdout pipe capacity configured"
            );
            return;
        }
        tracing::debug!(
            target: "bcs_bridge_core::engine",
            requested,
            error = %std::io::Error::last_os_error(),
            "engine stdout pipe capacity request rejected"
        );
    }
    tracing::warn!(
        target: "bcs_bridge_core::engine",
        "unable to enlarge engine stdout pipe; long JSONL events may be truncated by the engine"
    );
}

fn io_err(msg: &'static str) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::BrokenPipe, msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn cli_session_echo_and_kill() {
        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_engine.sh");
        let mut cli = CliSession::spawn(
            Path::new("bash"),
            &[script.to_string()],
            Path::new("."),
            &[],
            None,
        )
        .await
        .unwrap();
        cli.write_line("hello").await.unwrap();
        let line = cli.next_line().await.unwrap().unwrap();
        assert_eq!(line, "ack:hello");
        cli.kill().await;
    }

    #[tokio::test]
    async fn cancelled_read_preserves_partial_stdout_line() {
        let mut cli = CliSession::spawn(
            Path::new("bash"),
            &["-c".into(), "printf prefix; sleep 0.15; printf 'suffix\\n'; sleep 1".into()],
            Path::new("."), &[], None,
        ).await.unwrap();
        assert!(tokio::time::timeout(std::time::Duration::from_millis(70), cli.next_line()).await.is_err());
        assert_eq!(cli.next_line().await.unwrap().as_deref(), Some("prefixsuffix"));
        cli.kill().await;
    }

    #[tokio::test]
    async fn cli_session_close_stdin_blocks_further_writes() {
        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_engine.sh");
        let mut cli = CliSession::spawn(
            Path::new("bash"),
            &[script.to_string()],
            Path::new("."),
            &[],
            None,
        )
        .await
        .unwrap();
        cli.close_stdin();
        let err = cli.write_line("late").await.expect_err("write after close must fail");
        assert_eq!(err.kind(), std::io::ErrorKind::BrokenPipe);
        cli.kill().await;
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    #[allow(unsafe_code)]
    async fn cli_session_enlarges_stdout_pipe_for_long_jsonl_events() {
        use std::os::fd::AsRawFd;

        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/mock_engine.sh");
        let cli = CliSession::spawn(
            Path::new("bash"),
            &[script.to_string()],
            Path::new("."),
            &[],
            None,
        )
        .await
        .unwrap();
        let capacity = unsafe { libc::fcntl(cli.stdout.as_ref().unwrap().get_ref().as_raw_fd(), libc::F_GETPIPE_SZ) };
        assert!(capacity >= 8 * 1024, "engine stdout pipe remained too small: {capacity}");
        // `kill_on_drop` reaps the fixture without waiting here; the existing
        // kill-path tests cover the explicit async cleanup method.
        drop(cli);
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn cli_session_reads_nonblocking_long_jsonl_line_without_truncation() {
        let script = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/mock_engine_nonblocking_long_line.sh"
        );
        let mut cli = CliSession::spawn(
            Path::new("bash"),
            &[script.to_string()],
            Path::new("."),
            &[],
            None,
        )
        .await
        .unwrap();
        let line = cli
            .next_line()
            .await
            .unwrap()
            .expect("non-blocking fixture should emit a complete line");
        assert!(line.contains("\"type\":\"item.completed\""));
        assert_eq!(line.len(), 7068, "long JSONL event was truncated");
        drop(cli);
    }

    /// An engine that forks a long-lived grandchild, announcing its pid on
    /// the first stdout line. Both engine-tree tests below use it: an
    /// orphaned grandchild surviving the engine's death is exactly the leak
    /// that OOM'd the bridge host (engine → claude → MCP descendants were
    /// reparented to init and kept running).
    #[cfg(target_os = "linux")]
    async fn engine_with_grandchild() -> (CliSession, i32) {
        let mut cli = CliSession::spawn(
            Path::new("bash"),
            &["-c".into(), "sleep 60 & echo $!; sleep 55".into()],
            Path::new("."),
            &[],
            None,
        )
        .await
        .unwrap();
        let line = cli
            .next_line()
            .await
            .unwrap()
            .expect("engine must announce its grandchild pid");
        let pid: i32 = line.trim().parse().expect("engine announces a numeric pid");
        assert!(pid > 0);
        (cli, pid)
    }

    /// Whether `pid` still names a live (non-zombie) process. A zombie still
    /// owns its pid until reaped, so `kill(pid, 0)` alone would report dead
    /// children as alive; /proc state must agree.
    #[cfg(target_os = "linux")]
    fn process_alive(pid: i32) -> bool {
        let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else {
            return false;
        };
        // "pid (comm) state ..." — comm may contain ')' so parse after the last one.
        let after_comm = stat.rsplit(')').next().unwrap_or_default();
        !after_comm.trim_start().starts_with('Z')
    }

    /// Poll until `pid` is gone; returns true if it is STILL alive at timeout.
    #[cfg(target_os = "linux")]
    async fn still_alive_after(pid: i32, timeout: std::time::Duration) -> bool {
        let deadline = std::time::Instant::now() + timeout;
        while std::time::Instant::now() < deadline {
            if !process_alive(pid) {
                return false;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        process_alive(pid)
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn kill_terminates_the_whole_engine_process_tree() {
        let (mut cli, grandchild) = engine_with_grandchild().await;
        cli.kill().await;
        assert!(
            !still_alive_after(grandchild, std::time::Duration::from_secs(5)).await,
            "grandchild must not outlive the engine's kill"
        );
    }

    #[cfg(target_os = "linux")]
    #[tokio::test]
    async fn drop_terminates_the_whole_engine_process_tree() {
        // The bridge's Final path relies on Drop alone (no explicit kill):
        // cfuse spawns claude/MCP as descendants of the direct child, so a
        // single-pid kill_on_drop orphans the whole runtime tree.
        let (cli, grandchild) = engine_with_grandchild().await;
        drop(cli);
        assert!(
            !still_alive_after(grandchild, std::time::Duration::from_secs(5)).await,
            "grandchild must not survive CliSession drop"
        );
    }
}
