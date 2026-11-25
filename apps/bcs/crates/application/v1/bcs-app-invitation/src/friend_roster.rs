//! Owner-initiated friend removal (bot friend roster).
//!
//! `DeleteFriendConnection.actor` splits the V1 delete in two: absent keeps
//! caller-side self-removal byte-identical; present selects a managed-Bot
//! Owner removal — the caller must be the Bot's CURRENT owner
//! (`BotAuthorityPort.require_owner`, never the historical `created_by`), and
//! the revoke runs through `ConnectService::revoke_friend_as_owner` in the
//! orientation the durable implementation needs for its `RemovedByOwner`
//! notice.

use super::*;

impl InvitationFriendshipServiceImpl {
    /// Delete path split: an absent `actor` keeps the caller-side
    /// self-removal exactly as before; a present `actor` is an
    /// Owner-initiated removal of the Bot's friendship with `target_actor`.
    pub(crate) async fn delete_friend_connection(
        &self,
        command: DeleteFriendConnection,
    ) -> Result<DeleteResult, ApplicationError> {
        let connect = self.connect_service()?;
        let Some(actor) = &command.actor else {
            let caller = Self::caller_default_actor(&command.caller)?;
            let target = Self::actor_to_internal(&command.target_actor);
            let revoked = connect
                .revoke_friend(&caller, &target, command.request_auth.clone())
                .await
                .map_err(map_service_error)?;
            return Ok(DeleteResult {
                deleted: !revoked.is_empty(),
            });
        };
        // Owner-initiated removal: a Human caller (401 for anything else)
        // managing a concrete Bot — a Human-typed or id-less actor is invalid
        // input (defense in depth behind the route's both-or-none 400).
        let user = command
            .caller
            .user
            .as_ref()
            .ok_or(ApplicationError::Unauthenticated)?;
        if actor.actor_type != FriendConnectionActorType::Bot || actor.id.is_empty() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "actor must be a concrete Bot for owner-initiated friend removal",
            ));
        }
        self.load_bot(&actor.id).await?;
        // The destructive direction is Owner-only (CF-2): a live
        // `require_owner` edge, never the historical `created_by`.
        self.authority()?
            .require_owner(&user.id, &actor.id)
            .await
            .map_err(map_authority_error)?;
        // Orientation mirrors the notice the durable layer emits: a removed
        // Human travels first (the human→bot order keeps the friend
        // auth-sync revoke trigger firing); a removed friend Bot travels
        // second.
        let (first, second) = match command.target_actor.actor_type {
            FriendConnectionActorType::Human => (
                format!("human_{}", command.target_actor.id),
                actor.id.clone(),
            ),
            FriendConnectionActorType::Bot => {
                (actor.id.clone(), command.target_actor.id.clone())
            }
        };
        let revoked = connect
            .revoke_friend_as_owner(&first, &second, command.request_auth.clone())
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult {
            deleted: !revoked.is_empty(),
        })
    }
}
