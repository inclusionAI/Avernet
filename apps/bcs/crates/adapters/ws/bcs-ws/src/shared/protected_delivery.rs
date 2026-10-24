//! Protected Workbench run delivery (bot-authority spec §8.2/§8.3).
//!
//! The shared Run manager distinguishes two explicit sinks:
//!
//! - [`RunClientSink::Internal`] keeps the existing internal service
//!   String channel (internal HTTP Run consumers). The public writer
//!   can never consume an Internal sender.
//! - [`RunClientSink::Workbench`] is the protected Workbench sink:
//!   frames flow as typed [`OutboundFrame`]s through the
//!   per-connection bounded queue. Authorization happens at the
//!   ENQUEUE boundary against the operation's frozen per-connection
//!   permit ([`super::output_permits`]) — the socket writer never
//!   re-authorizes: an admitted operation's queued output is never
//!   retroactively revoked (spec §8.2 已准入输出不追溯).

use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use tokio::sync::mpsc;

/// The bounded protected queue depth of one Workbench connection.
/// The producer enqueues with backpressure on terminal frames and a
/// drop+metric policy on saturated data frames; the final writer
/// drains.
pub const PROTECTED_FRAME_QUEUE_SIZE: usize = 256;

/// Bounded backpressure budget for a TERMINAL frame whose connection
/// queue is full (spec §9 slow-consumer boundary): the producer waits
/// at most this long; a terminal still lost past the budget retires
/// the saturated connection as an explicit transport failure (see
/// [`super::connection_retirement`]) — never a silent live stream
/// without its final.
pub const TERMINAL_ENQUEUE_BUDGET: std::time::Duration = std::time::Duration::from_secs(2);

/// One Workbench run connection's outgoing frames. Every payload in
/// this queue was authorized at the enqueue boundary; the writer
/// simply sends in order.
#[derive(Debug, Clone)]
pub enum OutboundFrame {
    /// Operation output admitted by the operation's per-connection
    /// permit at the enqueue boundary.
    Data { payload: String },
    /// The terminal frame of an operation stream: typed so the
    /// producer applies bounded backpressure instead of a silent
    /// `try_send(Full)` drop (spec §9: terminal 不可静默丢失).
    Terminal { payload: String },
    /// Connection-control frame. Always deliverable — the public
    /// socket carries ONLY connection-control frames without an
    /// admitted permit.
    ConnectionControl { payload: String },
}

/// Trusted producer metadata consulted at the enqueue boundary. The
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
    /// Protected Workbench sink: typed frames plus the owning
    /// connection instance. A frame is enqueued only when the
    /// operation bound to this Run holds a permit for THIS
    /// connection instance — a replaced connection never matches.
    Workbench {
        tx: mpsc::Sender<OutboundFrame>,
        connection_instance_id: u64,
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
