//! Caller authorization and loadable-Group guards for the V1 Group facade.

use super::*;

impl GroupServiceImpl {
    /// The wired authority hook; failing to wire one is a
    /// composition error and must fail closed (never deny-as-Ok).
    pub(crate) fn authority(&self) -> Result<&dyn BotAuthorityPort, ApplicationError> {
        self.bot_authority
            .as_deref()
            .ok_or_else(|| {
                ApplicationError::internal(
                    "bot authority hook is not wired",
                )
            })
    }

    /// The CURRENT Human→Bot control judgment (row 8,
    /// bot-authority plan Task 6.3): a live `can_manage`
    /// edge for the verified User id. The legacy
    /// `created_by` fact and the legacy creator relation
    /// edge are history (spec §3.2) and no longer
    /// authorize anything here.
    pub(crate) async fn human_controls_bot(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, ApplicationError> {
        self.authority()?
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_authority_error)
    }

    /// Spec §8.3: the Group's authorization-fact
    /// participants, read through the strict snapshot
    /// Core against the MASTER data source. This
    /// instance's display cache (`try_get`) is never
    /// authoritative for a resource-role decision: a
    /// membership or scope change committed by another
    /// instance must invalidate a cached pass. An
    /// absent snapshot row grants no roles (the
    /// caller-side loaders refuse a missing row, and a
    /// live row always projects).
    pub(crate) async fn group_snapshot_participants(
        &self,
        group_id: &str,
    ) -> Result<Vec<bcs_service_api::types::authorization_snapshot::ParticipantAuthorizationSnapshot>, ApplicationError>
    {
        match self
            .groups
            .read_authorization_snapshot(group_id)
            .await
            .map_err(map_service_error)?
        {
            Some(snapshot) => Ok(snapshot.participants),
            None => Ok(Vec::new()),
        }
    }

    /// Spec §8.3: the Group's management-role actors
    /// (driver/originator/strategy-gated Manager),
    /// from the strict snapshot Core — the same
    /// freshness contract as
    /// [`Self::group_snapshot_participants`].
    pub(crate) async fn group_snapshot_management_actor_ids(
        &self,
        group_id: &str,
    ) -> Result<Vec<String>, ApplicationError> {
        match self
            .groups
            .read_authorization_snapshot(group_id)
            .await
            .map_err(map_service_error)?
        {
            Some(snapshot) => Ok(snapshot
                .management_actor_ids()
                .into_iter()
                .map(str::to_string)
                .collect()),
            None => Ok(Vec::new()),
        }
    }

    /// Hybrid identity resolution (row 10): the claim-local
    /// `HumanOrOwnedBot` selection is replaced by
    /// `resolve_authorized_principal` with the authenticated
    /// Bot as the requested `bot_id`. For hybrid User+Bot
    /// credentials the live management edge must pass BEFORE
    /// the Bot becomes the principal; pure Bot / pure Human
    /// credentials keep their existing selection (the
    /// resolver consults the hook only for the hybrid leg).
    pub(crate) async fn resolve_principal(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
    ) -> Result<Principal, ApplicationError> {
        match &caller.bot {
            Some(bot) => {
                resolve_authorized_principal(
                    caller,
                    IdentityPolicy::HumanOrAuthorizedBot {
                        bot_id: bot.bot_uuid.clone(),
                    },
                    self.authority()?,
                )
                .await
            }
            None => select_principal(caller, IdentityPolicy::HumanOrOwnedBot),
        }
    }

    pub(crate) async fn load_bot(
        &self,
        bot_uuid: &str,
    ) -> Result<bcs_service_api::RegisteredBot, ApplicationError> {
        self.registry
            .try_get(bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_uuid}' was not found"),
                )
            })
    }

    pub(crate) async fn resolve_view_actor(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        requested: Option<&str>,
    ) -> Result<String, ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let human_actor_id = format!("human_{}", user.id);
        let Some(requested) = requested else {
            return Ok(human_actor_id);
        };
        if requested == human_actor_id {
            return Ok(human_actor_id);
        }
        if requested.starts_with("human_") {
            return Err(ApplicationError::forbidden(
                "The explicit Human View Actor must identify the authenticated User",
            ));
        }
        let bot = self.load_bot(requested).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => {
                ApplicationError::forbidden("The explicit View Actor is not authorized")
            }
            other => other,
        })?;
        if bot.actor_kind == ActorKind::Bot
            && self.human_controls_bot(&user.id, requested).await?
        {
            Ok(requested.to_string())
        } else {
            Err(ApplicationError::forbidden(
                "The explicit View Actor is not authorized",
            ))
        }
    }

    pub(crate) async fn ensure_collaboration_eligible(
        &self,
        principal: &Principal,
        bot_uuid: &str,
        field_name: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                format!("{field_name} must identify a Bot Actor"),
            ));
        }
        if bot.status == ActorStatus::Hidden {
            return Err(ApplicationError::forbidden(format!(
                "Bot '{bot_uuid}' is hidden and cannot collaborate"
            )));
        }
        let principal_actor_id = principal.actor_id();
        if principal_actor_id == bot_uuid || bot.capabilities.visibility == "public" {
            return Ok(());
        }

        // Human sponsorship of a controlled, non-hidden Bot
        // (spec §8.1): a private/protected Group admits the
        // Bot when the Human's CURRENT management edge
        // (`can_manage`) authorizes it — friendship is not
        // required for this leg. The legacy `created_by`
        // fact and creator relation edge no longer sponsor.
        if let Principal::Human(human) = principal
            && self.human_controls_bot(&human.subject.id, bot_uuid).await?
        {
            return Ok(());
        }

        if self
            .friends
            .try_are_friends(&principal_actor_id, bot_uuid)
            .await
            .map_err(map_service_error)?
        {
            return Ok(());
        }

        Err(ApplicationError::forbidden(format!(
            "Bot '{bot_uuid}' is not collaboration-eligible for this Principal"
        )))
    }

    /// Bot actor ids participating in current Sessions of the Group —
    /// the candidate pool for the session-membership read fallback.
    /// Bounded to the first page (100 Sessions), consistent with the
    /// provisioning reconciler's fan-out budget.
    pub(crate) async fn group_session_bot_ids(
        &self,
        group_id: &str,
    ) -> Result<Vec<String>, ApplicationError> {
        let sessions = self
            .sessions
            .list_by_group(group_id, None, 0, 100, None, None)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        Ok(sessions
            .iter()
            .flat_map(|session| session.participants.iter())
            .filter(|participant| participant.actor_kind == ActorKind::Bot)
            .map(|participant| participant.bot_uuid.clone())
            .collect())
    }

    pub(crate) async fn can_read_group_detail(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        // Spec §8.3: the resource roles come from the
        // strict snapshot read, never this instance's
        // display cache.
        let participants = self
            .group_snapshot_participants(&group.id)
            .await?;
        if participants
            .iter()
            .any(|participant| participant.actor_id == principal_actor_id)
        {
            return Ok(true);
        }
        let mut session_actor_ids = match principal {
            Principal::Bot(_) => {
                if self
                    .group_snapshot_management_actor_ids(&group.id)
                    .await?
                    .iter()
                    .any(|actor_id| actor_id == &principal_actor_id)
                {
                    return Ok(true);
                }
                Vec::new()
            }
            Principal::Human(human) => {
                // Row 8 view leg: a Human reads through a participant Bot it
                // CURRENTLY controls (live `can_manage` per participant, never
                // a creator-list whitelist).
                for participant in participants
                    .iter()
                    .filter(|participant| participant.actor_kind == ActorKind::Bot)
                {
                    if self.human_controls_bot(&human.subject.id, &participant.actor_id).await? {
                        return Ok(true);
                    }
                }
                // Session fallback candidates: the caller itself (pushed below)
                // plus Session-only Bots it currently controls. A controlled
                // Group participant would already have read through the leg
                // above, and the authority cutover deliberately does not
                // consult the legacy creator list for this fallback.
                let mut controlled_session_bots = Vec::new();
                for bot_id in self.group_session_bot_ids(&group.id).await? {
                    if self.human_controls_bot(&human.subject.id, &bot_id).await? {
                        controlled_session_bots.push(bot_id);
                    }
                }
                controlled_session_bots
            }
        };
        session_actor_ids.push(principal_actor_id);
        self.sessions
            .has_group_session_participant(&group.id, &session_actor_ids)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))
    }

    /// The effective Human principal may act as itself or as a
    /// currently-controlled Bot originator. The effective Bot principal
    /// may act only as itself. Anything else is forbidden. This is stricter
    /// than legacy `authorize_originator` (which lets any human designate
    /// any originator); it is a V1 gate and legacy `POST /groups` is
    /// unaffected.
    pub(crate) async fn authorize_originator(
        &self,
        principal: &Principal,
        originator: &str,
    ) -> Result<(), ApplicationError> {
        if principal.actor_id() == originator {
            return Ok(());
        }
        let bot = self.load_bot(originator).await.map_err(|error| match error {
            ApplicationError::NotFound { .. } => ApplicationError::forbidden(format!(
                "Authenticated Principal cannot act as originator '{originator}'"
            )),
            other => other,
        })?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_originator",
                "originator must be a Bot Actor",
            ));
        }
        if let Principal::Human(human) = principal
            && self.human_controls_bot(&human.subject.id, originator).await?
        {
            return Ok(());
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated Principal cannot act as originator '{originator}'"
        )))
    }

    /// Structural validity only: the driver must resolve to a registered Bot
    /// Actor. Does NOT check any caller↔driver relationship (that was dropped
    /// by design) — it only rejects a non-existent or non-Bot driver id.
    pub(crate) async fn ensure_driver_is_registered_bot(
        &self,
        driver_bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let bot = self.load_bot(driver_bot_uuid).await?;
        if bot.actor_kind != ActorKind::Bot {
            return Err(ApplicationError::invalid(
                "invalid_participant",
                "driver_bot_uuid must identify a Bot Actor",
            ));
        }
        Ok(())
    }

    pub(crate) async fn can_read_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        // Spec §8.3: the resource roles (participants and
        // management anchors) come from the strict
        // snapshot read, not the display cache.
        let participants = self
            .group_snapshot_participants(&group.id)
            .await?;
        if participants
            .iter()
            .any(|participant| participant.actor_id == principal_actor_id)
        {
            return Ok(true);
        }
        let management_actor_ids = self
            .group_snapshot_management_actor_ids(&group.id)
            .await?;
        if management_actor_ids
            .iter()
            .any(|actor_id| actor_id == &principal_actor_id)
        {
            return Ok(true);
        }
        if let Principal::Human(human) = principal {
            let mut actor_ids = participants
                .iter()
                .map(|participant| participant.actor_id.clone())
                .collect::<Vec<_>>();
            actor_ids.extend(management_actor_ids);
            if self.human_can_act_as_any(human, actor_ids).await? {
                return Ok(true);
            }
        }
        let session_group_ids = self
            .sessions
            .list_group_ids_by_session_participant(&principal_actor_id)
            .await
            .map_err(|error| ApplicationError::internal(error.to_string()))?;
        Ok(session_group_ids.iter().any(|id| id == &group.id))
    }

    pub(crate) async fn human_actable_actor_id(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<Option<String>, ApplicationError> {
        let human_actor_id = format!("human_{}", human.subject.id);
        let mut seen = HashSet::new();
        for actor_id in actor_ids {
            if !seen.insert(actor_id.clone()) {
                continue;
            }
            if actor_id == human_actor_id {
                return Ok(Some(actor_id));
            }
            if actor_id.starts_with("human_") {
                continue;
            }
            let Some(bot) = self
                .registry
                .try_get(&actor_id)
                .await
                .map_err(map_service_error)?
            else {
                continue;
            };
            if bot.actor_kind != ActorKind::Bot {
                continue;
            }
            // Row 8 Human→Bot control leg: the CURRENT
            // `can_manage` edge decides; the historical
            // `created_by` and the creator relation edge
            // no longer authorize (spec §3.2).
            if self.human_controls_bot(&human.subject.id, &actor_id).await? {
                return Ok(Some(actor_id));
            }
        }
        Ok(None)
    }

    pub(crate) async fn human_can_act_as_any(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<bool, ApplicationError> {
        Ok(self.human_actable_actor_id(human, actor_ids).await?.is_some())
    }

    pub(crate) async fn principal_can_act_as(
        &self,
        principal: &Principal,
        actor_id: &str,
    ) -> Result<bool, ApplicationError> {
        if principal.actor_id() == actor_id {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => {
                self.human_can_act_as_any(human, vec![actor_id.to_string()]).await
            }
            Principal::Bot(_) => Ok(false),
        }
    }

    pub(crate) async fn resolve_group_manage_actor(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<Option<String>, ApplicationError> {
        let principal_actor_id = principal.actor_id();
        // Spec §8.3: the management anchors are
        // snapshot facts, re-read at the decision —
        // a cached `try_get` copy never elevates.
        let candidates = self
            .group_snapshot_management_actor_ids(&group.id)
            .await?;
        if candidates.iter().any(|actor_id| actor_id == &principal_actor_id) {
            return Ok(Some(principal_actor_id));
        }
        if let Principal::Human(human) = principal {
            return self.human_actable_actor_id(human, candidates).await;
        }
        Ok(None)
    }

    pub(crate) async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        Ok(self
            .resolve_group_manage_actor(principal, group)
            .await?
            .is_some())
    }

    pub(crate) async fn load_readable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_read_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    pub(crate) async fn load_group_detail_for_caller(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let principal = self.resolve_principal(caller).await?;
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if group.visibility != visibility_name(GroupVisibility::Public)
            && !self.can_read_group_detail(&principal, &group).await?
        {
            return Err(ApplicationError::forbidden(
                "The selected Principal has no readable relation to this Group",
            ));
        }
        Ok(group)
    }

    pub(crate) async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if group.record_status != "active" {
            return Err(ApplicationError::not_found(
                "group_not_found",
                format!("Group '{group_id}' was not found"),
            ));
        }
        if !self.can_manage_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Only the Group originator or driver may manage this Group",
            ));
        }
        Ok(group)
    }
}
