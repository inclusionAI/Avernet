use std::path::Path;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};

use super::trace::TraceContext;

pub struct CliSession {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<tokio::process::ChildStdout>,
    trace: Option<TraceContext>,
}

impl CliSession {
    pub async fn spawn(
        bin: &Path,
        args: &[String],
        cwd: &Path,
        env: &[(String, String)],
        trace: Option<TraceContext>,
    ) -> std::io::Result<Self> {
        let mut cmd = Command::new(bin);
        cmd.args(args)
            .current_dir(cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        for (k, v) in env {
            cmd.env(k, v);
        }
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io_err("stdin not piped"))?;
        let stdout = child.stdout.take().ok_or_else(|| io_err("stdout not piped"))?;
        #[cfg(target_os = "linux")]
        enlarge_stdout_pipe(&stdout);
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
        Ok(Self {
            child,
            stdin: Some(stdin),
            stdout: BufReader::new(stdout),
            trace,
        })
    }

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

    pub async fn next_line(&mut self) -> std::io::Result<Option<String>> {
        let mut line = String::new();
        let n = self.stdout.read_line(&mut line).await?;
        if n == 0 {
            return Ok(None);
        }
        if let Some(trace) = &self.trace {
            trace.record_raw(line.trim_end_matches(['\n', '\r']));
        }
        Ok(Some(line.trim_end_matches(['\n', '\r']).to_string()))
    }

    pub async fn kill(&mut self) {
        if let Err(e) = self.child.start_kill() {
            tracing::debug!(target: "bcs_bridge_core::engine", "kill failed: {e}");
        }
        let _ = self.child.wait().await;
    }
}

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
        let capacity = unsafe { libc::fcntl(cli.stdout.get_ref().as_raw_fd(), libc::F_GETPIPE_SZ) };
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
}
