//! Self-tests for the reusable assertions, not backend conformance evidence.
//! Scripted replies exercise the harnesses and deliberately violate their rules.
//! No runtime, persistence provider, or production wiring is introduced here.

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    application::{dist_gateway::*, dist_runtime::*},
    core::{dist_gateway::*, dist_runner::*},
    port::repo::distributed::{
        common::*, dispatch::*, inbox::*, ownership::*, result::*, session::*, task_feed::*,
    },
    port::{dist_agent::*, dist_runtime::DistRuntimeControlPort},
};
use bcs_test_support::contract::distributed::{self as harness, ContractResult};
use futures::{FutureExt, stream::BoxStream};
use std::{num::NonZeroU32, time::Duration};

#[path = "distributed_harness/script.rs"]
mod script;
use script::{Script, ok, rejects};
#[path = "distributed_harness/fixtures.rs"]
mod fixtures;
use fixtures::*;
#[path = "distributed_harness/dispatch.rs"]
mod dispatch_tests;
#[path = "distributed_harness/inbox.rs"]
mod inbox_tests;
#[path = "distributed_harness/ports.rs"]
mod ports;
#[path = "distributed_harness/recovery.rs"]
mod recovery_tests;
#[path = "distributed_harness/services.rs"]
mod service_tests;
#[path = "distributed_harness/session_spec.rs"]
mod session_spec_tests;
#[path = "distributed_harness/state.rs"]
mod state_tests;
#[path = "distributed_harness/task_feed.rs"]
mod task_feed_tests;
