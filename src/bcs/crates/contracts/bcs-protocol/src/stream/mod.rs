//! Engine-neutral strongly-typed streaming event protocol (`agent`/`chat`/`ping`).

pub mod agent;
pub mod event;
pub mod parse;
pub mod provider_text;

pub use agent::{
    ApprovalData, ApprovalPhase, LifecycleData, PhaseData, ThinkingData, ToolData, ToolPhase,
};
pub use event::{AgentData, AgentEvent, ChatEvent, ChatState, StreamEvent};
pub use parse::{audit_raw, parse_stream_event};
pub use provider_text::{
    ProviderTextEventState, ProviderTextResponseMode, apply_provider_event_text,
};
