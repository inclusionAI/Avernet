//! Engine-neutral strongly-typed streaming event protocol.

pub mod agent;
pub mod event;
pub mod parse;
pub mod provider_text;

pub use agent::{
    ApprovalData, ApprovalPhase, LifecycleData, PhaseData, ThinkingData, ToolData, ToolPhase,
};
pub use event::{
    AgentData, AgentEvent, ChatEvent, ChatState, InteractionEvent, InteractionKind,
    InteractionPhase, StreamEvent,
};
pub use parse::{RunEventV3Error, audit_raw, parse_run_event_v3, parse_stream_event};
pub use provider_text::{
    ProviderTextEventState, ProviderTextResponseMode, apply_provider_event_text,
};

/// Reserved adapter-to-application metadata. Delivery adapters must remove any
/// client-supplied value and stamp this key only after canonical validation.
pub const TASK_INTENT_ELIGIBLE_KEY: &str = "_bcs_task_intent_eligible";
