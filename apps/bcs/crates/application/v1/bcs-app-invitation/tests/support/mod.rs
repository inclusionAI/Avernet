//! Shared stateful fakes for the OpenAPI V1 invitation/friendship facade tests.
//!
//! The V1 `FriendshipService` facade is converged onto the legacy
//! `bcs_service_api::FriendService` use-case surface (backed by the
//! edge-permission connect use case). These fakes record the exact commands the
//! facade forwards and serve stateful results, so the tests assert the facade's
//! real forwarding, sorting and projection behavior instead of re-implementing
//! it in the test.

#![allow(dead_code, reason = "each test binary uses a subset of the fakes")]

use std::sync::Mutex;

use async_trait::async_trait;
use bcs_domain::edge_permission::{FriendListEntry as EdgeFriendListEntry, PermissionRequest};
use bcs_service_api::application::connect::{
    ConnectResult, ConnectService, FriendEntriesPage, FriendListQuery, RequestDirection,
    RequestsPage,
};
use bcs_service_api::{
    CreateFriendRequestCommand, FriendListEntry, FriendRequest, FriendRequestDecisionCommand,
    FriendRequestDirection, FriendRequestStatus, FriendService, FriendUseCaseError,
    ListFriendRequestsCommand, ListFriendsCommand, ServiceError, ServiceResult,
};

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().expect("fake mutex poisoned")
}

/// One recorded call into the legacy `FriendService` facade.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FriendCall {
    Create {
        caller_actor_id: String,
        to_bot: String,
    },
    List {
        caller_actor_id: String,
        direction: FriendRequestDirection,
        status: Option<FriendRequestStatus>,
    },
    Accept {
        caller_actor_id: String,
        request_id: String,
        request_to_bot: Option<String>,
    },
    Reject {
        caller_actor_id: String,
        request_id: String,
        request_to_bot: Option<String>,
    },
    Receiver {
        request_id: String,
    },
}

/// Stateful legacy `FriendService` fake.
///
/// Requests are stored in insertion order — like the legacy repo, which does
/// not order its rows — so any ordering in the facade's response is the
/// facade's own sort.
#[derive(Default)]
pub struct FakeFriendService {
    calls: Mutex<Vec<FriendCall>>,
    requests: Mutex<Vec<FriendRequest>>,
    next_id: Mutex<u64>,
    clock: Mutex<u64>,
    create_forbidden: Mutex<Option<String>>,
    create_error: Mutex<Option<ServiceError>>,
    list_error: Mutex<Option<ServiceError>>,
}

impl FakeFriendService {
    pub fn new() -> Self {
        let fake = Self::default();
        *lock(&fake.clock) = 1_000;
        fake
    }

    /// Every call the facade forwarded, in order.
    pub fn calls(&self) -> Vec<FriendCall> {
        lock(&self.calls).clone()
    }

    /// Seed a request row exactly as the legacy store would hold it.
    pub fn seed(&self, request: FriendRequest) {
        lock(&self.requests).push(request);
    }

    /// Current legacy status of a seeded/created request.
    pub fn status_of(&self, request_id: &str) -> Option<FriendRequestStatus> {
        lock(&self.requests)
            .iter()
            .find(|request| request.id == request_id)
            .map(|request| request.status.clone())
    }

    pub fn fail_create_with(&self, error: ServiceError) {
        *lock(&self.create_error) = Some(error);
    }

    pub fn forbid_create(&self, message: impl Into<String>) {
        *lock(&self.create_forbidden) = Some(message.into());
    }

    pub fn fail_list_with(&self, error: ServiceError) {
        *lock(&self.list_error) = Some(error);
    }

    fn next_timestamp(&self) -> u64 {
        let mut clock = lock(&self.clock);
        let now = *clock;
        *clock += 10;
        now
    }

    fn update_status(
        &self,
        request_id: &str,
        next: FriendRequestStatus,
    ) -> Result<(), FriendUseCaseError> {
        let mut requests = lock(&self.requests);
        let Some(request) = requests.iter_mut().find(|request| request.id == request_id) else {
            return Err(FriendUseCaseError::service(
                ServiceError::FriendRequestNotFound(request_id.to_string()),
            ));
        };
        match (&request.status, &next) {
            (FriendRequestStatus::Rejected, FriendRequestStatus::Accepted) => {
                return Err(FriendUseCaseError::service(
                    ServiceError::CannotAcceptRejected,
                ));
            }
            (FriendRequestStatus::Accepted, FriendRequestStatus::Rejected) => {
                return Err(FriendUseCaseError::service(
                    ServiceError::CannotRejectAccepted,
                ));
            }
            _ => {}
        }
        request.status = next;
        request.updated_at = self.next_timestamp();
        Ok(())
    }
}

#[async_trait]
impl FriendService for FakeFriendService {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError> {
        lock(&self.calls).push(FriendCall::Create {
            caller_actor_id: command.caller_actor_id.clone(),
            to_bot: command.to_bot.clone(),
        });
        if let Some(message) = lock(&self.create_forbidden).take() {
            return Err(FriendUseCaseError::Forbidden(message));
        }
        if let Some(error) = lock(&self.create_error).take() {
            return Err(FriendUseCaseError::service(error));
        }
        let id = {
            let mut next_id = lock(&self.next_id);
            *next_id += 1;
            format!("req-{next_id}")
        };
        let now = self.next_timestamp();
        let request = FriendRequest {
            id,
            from_bot: command.caller_actor_id,
            to_bot: command.to_bot,
            status: FriendRequestStatus::Pending,
            created_at: now,
            updated_at: now,
        };
        lock(&self.requests).push(request.clone());
        Ok(request)
    }

    async fn list_friend_requests(
        &self,
        command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
        lock(&self.calls).push(FriendCall::List {
            caller_actor_id: command.caller_actor_id.clone(),
            direction: command.direction.clone(),
            status: command.status_filter.clone(),
        });
        if let Some(error) = lock(&self.list_error).take() {
            return Err(FriendUseCaseError::service(error));
        }
        Ok(lock(&self.requests)
            .iter()
            .filter(|request| match command.direction {
                FriendRequestDirection::Sent => request.from_bot == command.caller_actor_id,
                FriendRequestDirection::Received => request.to_bot == command.caller_actor_id,
                FriendRequestDirection::All => {
                    request.from_bot == command.caller_actor_id
                        || request.to_bot == command.caller_actor_id
                }
            })
            .filter(|request| {
                command
                    .status_filter
                    .as_ref()
                    .is_none_or(|status| &request.status == status)
            })
            .cloned()
            .collect())
    }

    async fn accept_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        lock(&self.calls).push(FriendCall::Accept {
            caller_actor_id: command.caller_actor_id.clone(),
            request_id: command.request_id.clone(),
            request_to_bot: command.request_to_bot.clone(),
        });
        self.update_status(&command.request_id, FriendRequestStatus::Accepted)
    }

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        lock(&self.calls).push(FriendCall::Reject {
            caller_actor_id: command.caller_actor_id.clone(),
            request_id: command.request_id.clone(),
            request_to_bot: command.request_to_bot.clone(),
        });
        self.update_status(&command.request_id, FriendRequestStatus::Rejected)
    }

    async fn friend_request_receiver(
        &self,
        request_id: &str,
    ) -> Result<String, FriendUseCaseError> {
        lock(&self.calls).push(FriendCall::Receiver {
            request_id: request_id.to_string(),
        });
        lock(&self.requests)
            .iter()
            .find(|request| request.id == request_id)
            .map(|request| request.to_bot.clone())
            .ok_or_else(|| {
                FriendUseCaseError::service(ServiceError::FriendRequestNotFound(
                    request_id.to_string(),
                ))
            })
    }

    async fn list_friends(
        &self,
        _command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        Ok(Vec::new())
    }
}

/// `ConnectService` fake: only `revoke_friend` is exercised by the V1
/// friendship facade; every other connect entry point is a wiring bug for these
/// tests and panics.
#[derive(Default)]
pub struct FakeConnectService {
    friendships: Mutex<Vec<(String, String)>>,
    revoke_calls: Mutex<Vec<(String, String)>>,
    revoke_error: Mutex<Option<ServiceError>>,
}

impl FakeConnectService {
    pub fn new() -> Self {
        Self::default()
    }

    /// Seed a symmetric friendship edge the revoke path can remove.
    pub fn add_friendship(&self, bot_uuid: &str, friend_bot_uuid: &str) {
        lock(&self.friendships).push((
            bot_uuid.to_string(),
            friend_bot_uuid.to_string(),
        ));
    }

    /// Every `revoke_friend(caller, target)` call the facade made, in order.
    pub fn revoke_calls(&self) -> Vec<(String, String)> {
        lock(&self.revoke_calls).clone()
    }

    pub fn fail_revoke_with(&self, error: ServiceError) {
        *lock(&self.revoke_error) = Some(error);
    }
}

#[async_trait]
impl ConnectService for FakeConnectService {
    async fn create_connect(
        &self,
        _caller: &str,
        _to_bot: &str,
        _message: Option<String>,
        _request_auth: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<ConnectResult> {
        unreachable!("the V1 friendship facade must not call create_connect")
    }

    async fn approve(
        &self,
        _request_id: &str,
        _decider: &str,
        _request_auth: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        unreachable!("the V1 friendship facade must not call approve")
    }

    async fn reject(
        &self,
        _request_id: &str,
        _decider: &str,
        _reason: Option<String>,
    ) -> ServiceResult<()> {
        unreachable!("the V1 friendship facade must not call reject")
    }

    async fn cancel(&self, _request_id: &str) -> ServiceResult<()> {
        unreachable!("the V1 friendship facade must not call cancel")
    }

    async fn get_request(&self, _request_id: &str) -> ServiceResult<PermissionRequest> {
        unreachable!("the V1 friendship facade must not call get_request")
    }

    async fn revoke_friend(
        &self,
        caller: &str,
        target: &str,
        _request_auth: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        lock(&self.revoke_calls).push((caller.to_string(), target.to_string()));
        if let Some(error) = lock(&self.revoke_error).take() {
            return Err(error);
        }
        let mut friendships = lock(&self.friendships);
        let before = friendships.len();
        friendships.retain(|(left, right)| {
            !((left == caller && right == target) || (left == target && right == caller))
        });
        let revoked = before - friendships.len();
        Ok((1..=revoked as u64).collect())
    }

    async fn list_friends(&self, _actor: &str) -> ServiceResult<Vec<EdgeFriendListEntry>> {
        unreachable!("the V1 friendship facade must not call list_friends")
    }

    async fn list_friends_paginated(
        &self,
        _actor: &str,
        _query: FriendListQuery,
    ) -> ServiceResult<FriendEntriesPage> {
        unreachable!("the V1 friendship facade must not call list_friends_paginated")
    }

    async fn list_requests(
        &self,
        _actor: &str,
        _direction: RequestDirection,
        _status: Option<bcs_domain::edge_permission::RequestStatus>,
        _page: u32,
        _page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        unreachable!("the V1 friendship facade must not call list_requests")
    }
}