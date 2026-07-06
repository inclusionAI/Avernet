//! Outbound port for controlling a bot's runtime connection.
//!
//! Today the only control operation is "kick" — used by the
//! switch-delivery-to-provider use case to terminate the obsolete
//! WebSocket connection once a provider binding takes effect.

use async_trait::async_trait;

/// Why a bot connection is being torn down. Sent to the bot in the
/// `bot.kicked` event payload before the socket closes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KickReason {
    /// Delivery channel has been switched from WebSocket to HttpProvider.
    DeliverySwitchedToProvider,
}

impl KickReason {
    /// Stable wire string used in event payloads and logs.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::DeliverySwitchedToProvider => "delivery_switched_to_provider",
        }
    }
}

/// Outbound port for terminating a bot's currently-attached runtime
/// connection. Implementations are process-local; multi-replica deploys
/// only reach the replica holding the socket.
#[async_trait]
pub trait BotConnectionControlPort: Send + Sync {
    /// Send a `bot.kicked` event to the bot (if connected) and close the
    /// socket. Returns true iff a live connection was actually torn down.
    async fn kick(&self, bot_id: &str, reason: KickReason) -> bool;
}
