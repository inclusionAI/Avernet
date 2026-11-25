//! Strict approved-friend reads shared by the v1 candidate lists.
//!
//! Approved Connect/edge relationships are the only friend authority here.
//! The legacy friend store has no error channel, so a SQL failure could be
//! observed as a successful empty set; this module reads the fallible
//! `ConnectService::list_friends_paginated` page API instead.

use std::collections::HashSet;

use bcs_service_api::ActorKind;
use bcs_service_api::application::connect::FriendListQuery;

use crate::{ApplicationError, BotServiceImpl, map_service_error};

/// Upper bound accepted by the edge store's friend page query (a larger limit
/// is rejected there as `InvalidOperation`).
const FRIEND_PAGE_LIMIT: u32 = 100;

impl BotServiceImpl {
    /// Collect every approved Bot friend of `actor_id` through bounded pages.
    ///
    /// The first page fixes this request's total budget: later page drift,
    /// short pages, duplicate identities or non-Bot entries fail the whole
    /// read instead of returning a partial set. Each business request performs
    /// its own read; no cross-request cache masks the cost.
    pub(crate) async fn approved_bot_friend_ids(
        &self,
        actor_id: &str,
    ) -> Result<HashSet<String>, ApplicationError> {
        let mut ids = HashSet::new();
        let mut offset = 0_u64;
        let mut first_total = None;
        loop {
            let page = self
                .connect_service
                .list_friends_paginated(
                    actor_id,
                    FriendListQuery {
                        target_type: Some(ActorKind::Bot),
                        offset,
                        limit: FRIEND_PAGE_LIMIT,
                    },
                )
                .await
                .map_err(map_service_error)?;
            let total = *first_total.get_or_insert(page.total);
            let expected = total
                .saturating_sub(offset)
                .min(u64::from(FRIEND_PAGE_LIMIT));
            if page.total != total || page.items.len() as u64 != expected {
                return Err(ApplicationError::internal(
                    "friend page changed; retry the request",
                ));
            }
            for entry in page.items {
                if entry.kind != ActorKind::Bot
                    || entry.actor_id.is_empty()
                    || !ids.insert(entry.actor_id)
                {
                    return Err(ApplicationError::internal(
                        "invalid or changed friend page",
                    ));
                }
            }
            offset += expected;
            if offset >= total {
                return Ok(ids);
            }
        }
    }
}
