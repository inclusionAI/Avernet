//! Transport-only connection epochs, driven by bootstrap leadership supervision.
use std::sync::Mutex;
use tokio_util::sync::CancellationToken;

/// A cancelled epoch stays cancelled even if this instance becomes leader again.
/// This also closes sockets accepted while admission is disabled, including
/// sockets which have not sent their first protocol frame yet.
#[derive(Debug)]
pub struct ConnectionEpoch(Mutex<CancellationToken>);

impl Default for ConnectionEpoch {
    fn default() -> Self {
        Self(Mutex::new(CancellationToken::new()))
    }
}

impl ConnectionEpoch {
    pub fn subscribe(&self) -> CancellationToken {
        self.0.lock().expect("connection epoch lock poisoned").child_token()
    }

    pub fn set_accepting(&self, accepting: bool) {
        let mut epoch = self.0.lock().expect("connection epoch lock poisoned");
        if accepting {
            if epoch.is_cancelled() {
                *epoch = CancellationToken::new();
            }
        } else {
            epoch.cancel();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn demotion_closes_existing_and_late_sockets_without_reviving_old_epoch() {
        let gate = ConnectionEpoch::default();
        let old = gate.subscribe();
        gate.set_accepting(true);
        assert!(!old.is_cancelled());
        gate.set_accepting(false);
        assert!(old.is_cancelled());
        assert!(gate.subscribe().is_cancelled());
        gate.set_accepting(false);
        gate.set_accepting(true);
        assert!(old.is_cancelled());
        let new = gate.subscribe();
        assert!(!new.is_cancelled());
        // A single connection cannot cancel its siblings or the whole epoch.
        new.cancel();
        assert!(!gate.subscribe().is_cancelled());
    }
}
