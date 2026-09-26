//! Contract harness for terminal-event observer ports.

use std::future::Future;

use bcs_service_api::{BotTerminalEvent, BotTerminalObserverPort};

/// Verifies that observing an event produces the implementation's externally
/// observable effect. The probe future must inspect that effect rather than the
/// observer's internal state.
pub async fn bot_terminal_observer_port_contract_tests<T, F>(
    port: &T,
    event: BotTerminalEvent,
    observed_effect: F,
) where
    T: BotTerminalObserverPort + ?Sized,
    F: Future<Output = bool>,
{
    port.observe(event).await;
    assert!(
        observed_effect.await,
        "terminal observer must produce its configured observable effect"
    );
}
