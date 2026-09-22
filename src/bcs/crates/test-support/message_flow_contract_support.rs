//! Message-flow contract test doubles, split by concern. Every `pub` item stays
//! reachable here (re-exported), so test binaries keep including this file via
//! `#[path = ".../message_flow_contract_support.rs"]` unchanged.
#![allow(dead_code)]

#[path = "message_flow_contract/delivery_recordings.rs"]
mod delivery_recordings;
#[path = "message_flow_contract/flow_test_support.rs"]
mod flow_test_support;
#[path = "message_flow_contract/group_core_fakes.rs"]
mod group_core_fakes;
#[path = "message_flow_contract/human_notify_recorder.rs"]
mod human_notify_recorder;
#[path = "message_flow_contract/registry_routing_fakes.rs"]
mod registry_routing_fakes;

#[allow(unused_imports)]
pub use delivery_recordings::{RecordingBotDelivery, RecordingFrontendDelivery};
#[allow(unused_imports)]
pub use flow_test_support::FlowTestSupport;
#[allow(unused_imports)]
pub use group_core_fakes::{FakeGroupCoreService, NotifyPolicyProbe};
#[allow(unused_imports)]
pub use human_notify_recorder::RecordingHumanMentionNotify;
#[allow(unused_imports)]
pub use registry_routing_fakes::{FakeRegistryService, FakeRoutingCoreService};
