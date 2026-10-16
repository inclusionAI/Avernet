//! Protected Workbench run delivery (bot-authority spec §8.2/§8.3).
//!
//! The shared Run manager distinguishes two explicit sinks:
//!
//! - [`RunClientSink::Internal`] keeps the existing internal service
//!   String channel (internal HTTP Run consumers). The public writer
//!   can never consume an Internal sender.
//! - [`RunClientSink::Workbench`] is the protected Workbench sink:
//!   typed frames plus the connection's
//!   [`ProtectedDeliveryBinding`]. Frames flow as
//!   [`OutboundFrame::Protected`] through a per-connection bounded
//!   queue and are finally authorized per frame at the socket
//!   writer ([`super::super::web`]).

use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use bcs_service_api::application::v1::ProtectedDeliveryBinding;
use tokio::sync::mpsc;

/// The bounded protected queue depth of one Workbench connection.
/// The producer enqueues with backpressure; the final writer drains
/// and authorizes per frame.
pub const PROTECTED_FRAME_QUEUE_SIZE: usize = 256;

/// One Workbench run connection's outgoing frames.
#[derive(Debug, Clone)]
pub enum OutboundFrame {
    /// Protected data: authorized against the CURRENT authorization
    /// facts immediately before the socket writer sends it. No
    /// cached connection-time scope or enqueue-time decision.
    Protected {
        binding: ProtectedDeliveryBinding,
        visibility: DeliveryVisibility,
        payload: String,
    },
    /// Connection-control frame. Always deliverable — the public
    /// socket carries ONLY connection-control frames without a
    /// passing delivery authorization.
    ConnectionControl { payload: String },
}

/// Trusted producer metadata carried with a protected frame. The
/// application contract type is reused — no synonymous adapter enum.
pub type DeliveryVisibility = bcs_service_api::application::v1::DeliveryVisibility;

/// Build the trusted producer metadata for one frame.
pub fn visibility(
    domain: MessageVisibilityDomain,
    audience: Option<&MessageAudience>,
) -> DeliveryVisibility {
    DeliveryVisibility {
        domain,
        audience: audience.cloned(),
    }
}

/// The explicit sink types of the shared Run manager (plan Task 7:
/// `RunClientSink = Internal | Workbench`).
#[derive(Debug, Clone)]
pub enum RunClientSink {
    /// Existing internal service consumer sink (raw String sender,
    /// e.g. the internal HTTP Run drain loop). Its data signature is
    /// preserved; it is never a Workbench downgrade channel.
    Internal { tx: mpsc::Sender<String> },
    /// Protected Workbench sink: typed frames plus the connection's
    /// binding.
    Workbench {
        tx: mpsc::Sender<OutboundFrame>,
        binding: ProtectedDeliveryBinding,
    },
}

impl RunClientSink {
    /// Whether this sink belongs to the same connection instance.
    pub fn is_same_connection(&self, other: &RunClientSink) -> bool {
        match (self, other) {
            (
                RunClientSink::Workbench { tx, .. },
                RunClientSink::Workbench { tx: other_tx, .. },
            ) => tx.same_channel(other_tx),
            _ => false,
        }
    }
}
