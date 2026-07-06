use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum BotDeliveryKind {
    Send,
    Inject,
    Abort,
    TaskDispatch,
    TaskMessage,
    TaskResult,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum FrontendDeliveryKind {
    WorkbenchEvent,
    RunEvent,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum FrontendDeliveryTarget {
    Group { group_id: String },
    Session { session_id: String },
    Run { run_id: String },
}
