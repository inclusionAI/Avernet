//! Opt-in diagnostic timing; no message bodies, SQL or credentials are logged.
pub(crate) struct Timer {
    stage: &'static str,
    started: Option<std::time::Instant>,
}
impl Timer {
    pub(crate) fn new(stage: &'static str) -> Self {
        Self { stage, started: tracing::enabled!(target: "bcs_reply_profile", tracing::Level::DEBUG).then(std::time::Instant::now) }
    }
}
impl Drop for Timer {
    fn drop(&mut self) {
        if let Some(started) = self.started {
            tracing::debug!(target: "bcs_reply_profile", stage = self.stage, elapsed_us = started.elapsed().as_micros() as u64, "reply stage elapsed");
        }
    }
}
