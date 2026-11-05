//! In-test tracing capture for the application log-redaction tests
//! (plan Task 10 step 4a): a writer buffer installed as the thread
//! default `tracing` subscriber for the duration of one flow, with the
//! level floor at DEBUG so every category event is captured. The tests
//! run on a current-thread runtime, so awaiting inside the guard scope
//! keeps every event captured.
//!
//! Compiled once per integration test binary; not every binary captures
//! logs, so unused helpers are expected.
#![allow(dead_code)]

use std::io::Write;
use std::sync::{Arc, Mutex};

/// Shared byte buffer the subscriber writes into.
#[derive(Clone, Default)]
pub struct LogBuffer(Arc<Mutex<Vec<u8>>>);

impl LogBuffer {
    /// The captured log text so far.
    pub fn captured(&self) -> String {
        let bytes = self.0.lock().expect("log buffer mutex").clone();
        String::from_utf8(bytes).expect("captured logs are UTF-8")
    }
}

/// Private writer handle for the shared buffer (the `MakeWriter`
/// associated type must be nameable; it is never formatted).
pub struct BufferWriter(Arc<Mutex<Vec<u8>>>);

impl Write for BufferWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0
            .lock()
            .expect("log buffer mutex")
            .extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for LogBuffer {
    type Writer = BufferWriter;

    fn make_writer(&'a self) -> Self::Writer {
        BufferWriter(self.0.clone())
    }
}

/// Install a capturing fmt subscriber (level floor DEBUG) as the
/// thread default and return the buffer plus the scope guard. As long
/// as the guard lives, every event the orchestration emits on this
/// thread — successes and failures alike — is captured.
pub fn install_debug_capture() -> (LogBuffer, tracing::subscriber::DefaultGuard) {
    let buffer = LogBuffer::default();
    let subscriber = tracing_subscriber::fmt()
        .with_ansi(false)
        .with_level(true)
        .with_target(false)
        .with_max_level(tracing::Level::DEBUG)
        .with_writer(buffer.clone())
        .finish();
    let guard = tracing::subscriber::set_default(subscriber);
    (buffer, guard)
}
