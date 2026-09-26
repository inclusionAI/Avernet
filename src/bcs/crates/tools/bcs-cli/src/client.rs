//! HTTP helper used by the `bcs-cli` binaries.
//!
//! This is a tool-local client, not a public SDK. Shared wire DTOs live in
//! `bcs-protocol`.

use std::collections::{BTreeMap, HashMap};

use std::time::Duration;


use anyhow::{Context, Result, anyhow};

use reqwest::header::HeaderValue;

use serde::Deserialize;

use tracing::{debug, info, warn};


use bcs_protocol::{
    BCS_CHAT_VERSION, BCS_CHAT_VERSION_HEADER, BindingChannels, BotCapabilities, BotConnectParams,
    BotConnectResponse, BotDynamicStatus, BotInfo, ChatRunCancelResponse, ChatRunState,
    ChatRunStatusResponse, ChatRunSubmitResponse, ConfirmProposalResponse, CreateGroupRequest,
    CreateGroupResponse, DiscoverBotsExtendedResponse, DiscoverBotsResponse, FriendApiResponse,
    FusionRequest, FusionResponse, JoinRequest, JoinResponse, OnboardRequest, OnboardResponse,
    ParticipantBindingInfo, ParticipantInfo, ProposalResponse, QueryBotEntry, QueryBotsRequest,
    SetVisibilityRequest, Skill, UpdateStatusRequest, UpdateStatusResponse,
};


#[cfg(test)]
#[path = "client_tests/mod.rs"]
mod tests;



#[path = "client/default_bcs_url.rs"]
mod default_bcs_url;
#[cfg(test)]
use default_bcs_url::DEFAULT_BCS_URL;
pub use default_bcs_url::{BotGroupListPage, CreateCustomGroupOptions, RunSessionCollaborationOptions, CurrentActorGroupListPage, BcsClient, ChatRunOutcome, ChatAsyncError};
#[path = "client/get_bot.rs"]
mod get_bot;
#[path = "client/create_custom_group_with_initial_session.rs"]
mod create_custom_group_with_initial_session;
#[path = "client/session_chat.rs"]
mod session_chat;
