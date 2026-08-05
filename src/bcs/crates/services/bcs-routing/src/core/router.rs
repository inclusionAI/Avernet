//! Message Routing Service Implementation.
//!
//! This crate provides the concrete implementation of `RoutingCoreService`
//! for routing messages to bots based on @mentions.
//!
//! Key principle: ALL messages are broadcast to ALL participants.
//! @mentions determine delivery_type (Send vs Inject), not routing targets.
//!
//! Delivery rules:
//! - If sender_bot_id matches a participant bot, exclude it from targets
//! - If sender_bot_id is None or not a participant (real person), broadcast to ALL
//! - @mentioned bots → DeliveryType::Send
//! - No @mention → coordinator/driver gets DeliveryType::Send, others get Inject
//! - @ALL → everyone gets DeliveryType::Send
//!
//! Real Person Participation:
//! When a real person (not a bot) sends a message, the 'from' field contains
//! a user identifier that doesn't match any participant bot_id. In this case,
//! all participants receive the message, including the person's own bot.
//!
//! Note: Bot communication is handled via WebSocket only. The routing
//! service identifies which bots should receive messages, but actual
//! delivery is handled by BCS's WebSocket connection manager.

use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use regex::Regex;
use tracing::{debug, info, warn};

const MSG_LOG_TARGET: &str = "bcs_message";

fn truncate_preview(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((idx, _)) => &s[..idx],
        None => s,
    }
}

use bcs_service_api::{
    ActorKind, ActorStatus, BotRegistryCoreService, BotSendResult, ChatEventRouting, DeliveryType,
    Group, GroupKind, GroupStrategy, HiddenMentionInfo, Participant, ParticipantMode,
    ParticipantRole, ResponseMode, RouteAndSendResult, RouteParticipantOverlay,
    RouteSelectorWire, RoutingCoreService, RoutingDecision, RoutingTarget,
    StructuredRoutingError,
};

/// Whether `participant` is the lead participant for the given group strategy.
///
/// The lead participant is the one that receives `chat.send` when no
/// @-mention targets anyone. It is strategy-dependent:
/// - `GroupStrategy::Chat` → role == Driver
/// - `GroupStrategy::ManagerWorker` → role == Manager
fn is_lead_participant(strategy: GroupStrategy, participant: &Participant) -> bool {
    participant.role == strategy.lead_role()
}

// ---------------------------------------------------------------------------
// X.3 Overlay helpers (work on the canonical RouteParticipantOverlay
// defined in `bcs_services` so callers and the router share one type).
// ---------------------------------------------------------------------------

/// Effective mode after defaulting by actor_kind for the overlay row.
///
/// `None` in `overlay.mode` means "use the actor-kind default" (Bot→Auto,
/// Human→Absent), matching `Participant::mode`'s wire shape.
fn overlay_effective_mode(overlay: &RouteParticipantOverlay) -> ParticipantMode {
    overlay
        .mode
        .unwrap_or_else(|| ParticipantMode::default_for(overlay.actor_kind))
}

/// Whether routing should force a `Send` decision down to `Inject` for
/// this participant. Per design §4.5:
/// - mode=muted → muted Bot still observes (Inject), never auto-replies
/// - status=hidden → actor is "incognito"; even @-targeted messages
///   become Inject so they reach transcript without triggering a
///   visible response (X.7 layers `silent=true` on the WS event).
fn overlay_forced_inject(overlay: &RouteParticipantOverlay) -> bool {
    overlay_effective_mode(overlay) == ParticipantMode::Muted
        || overlay.status == ActorStatus::Hidden
}

fn is_display_name_mention_boundary(remainder: &str) -> bool {
    let Some(next) = remainder.chars().next() else {
        return true;
    };
    next.is_whitespace()
        || matches!(
            next,
            ',' | '，'
                | '.'
                | '。'
                | '!'
                | '！'
                | '?'
                | '？'
                | ';'
                | '；'
                | '、'
                | '：'
                | '@'
                | '*'
                | '`'
                | '"'
                | '\''
                | ')'
                | '）'
                | ']'
                | '】'
                | '}'
                | '》'
        )
}

// ---------------------------------------------------------------------------
// Structured Routing Types
// ---------------------------------------------------------------------------

/// Internal route selector enum (parsed from wire RouteSelectorWire).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RouteSelector {
    /// Select a specific bot by UUID.
    Bot { bot_uuid: String },
    /// Select a bot by display name.
    Name { bot_name: String },
    /// Select bots by group role (driver/consultant/observer).
    Role { role: String },
    /// Select bots by capability tag (exact match against skills/domains).
    Capability { value: String },
    /// Select all participants in the group.
    ParticipantsAll,
    /// Select all participants except the sender.
    ParticipantsOthers,
    /// Select the group originator/coordinator.
    Originator,
    /// Select the group driver.
    Driver,
}

/// Structured route request input for route_structured().
#[derive(Debug, Clone)]
pub struct StructuredRouteRequest {
    pub group_id: String,
    pub from_bot_uuid: String,
    pub responders: Vec<RouteSelector>,
    pub mode: ResponseMode,
    pub reason: String,
    pub include_self: bool,
    pub dedupe_key: Option<String>,
}

/// Error when converting wire selector to internal representation.
#[derive(Debug, Clone, thiserror::Error)]
pub enum RouteSelectorError {
    #[error("unsupported selector type: {0}")]
    UnsupportedType(String),
    #[error("missing value for selector type: {0}")]
    MissingValue(String),
}

impl TryFrom<&RouteSelectorWire> for RouteSelector {
    type Error = RouteSelectorError;

    fn try_from(wire: &RouteSelectorWire) -> Result<Self, Self::Error> {
        match wire.selector_type.as_str() {
            "bot" => {
                let val = wire
                    .value
                    .as_deref()
                    .ok_or_else(|| RouteSelectorError::MissingValue("bot".to_string()))?;
                Ok(RouteSelector::Bot {
                    bot_uuid: val.to_string(),
                })
            }
            "name" => {
                let val = wire
                    .value
                    .as_deref()
                    .ok_or_else(|| RouteSelectorError::MissingValue("name".to_string()))?;
                Ok(RouteSelector::Name {
                    bot_name: val.to_string(),
                })
            }
            "role" => {
                let val = wire
                    .value
                    .as_deref()
                    .ok_or_else(|| RouteSelectorError::MissingValue("role".to_string()))?;
                Ok(RouteSelector::Role {
                    role: val.to_string(),
                })
            }
            "capability" => {
                let val = wire
                    .value
                    .as_deref()
                    .ok_or_else(|| RouteSelectorError::MissingValue("capability".to_string()))?;
                Ok(RouteSelector::Capability {
                    value: val.to_string(),
                })
            }
            "participants" => {
                match wire.value.as_deref() {
                    Some("all") => Ok(RouteSelector::ParticipantsAll),
                    Some("others") => Ok(RouteSelector::ParticipantsOthers),
                    Some(v) => Err(RouteSelectorError::UnsupportedType(format!(
                        "participants:{v}"
                    ))),
                    None => Ok(RouteSelector::ParticipantsAll), // default to all
                }
            }
            "originator" => Ok(RouteSelector::Originator),
            "driver" => Ok(RouteSelector::Driver),
            other => Err(RouteSelectorError::UnsupportedType(other.to_string())),
        }
    }
}

/// In-memory implementation of RoutingCoreService.
#[derive(Debug)]
pub struct MessageRouter {
    /// Regex for @mentions.
    mention_regex: Regex,
}

impl MessageRouter {
    /// Create a new message router.
    pub fn new() -> Self {
        Self {
            mention_regex: Regex::new(r"@([-\w\p{Unified_Ideograph}:]+)")
                .expect("Invalid mention regex"),
        }
    }

    fn resolve_mentions(&self, session: &Group, message: &str) -> Vec<String> {
        let mut resolved_mentions = Vec::new();
        for (at_index, _) in message.match_indices('@') {
            let after_at = &message[at_index + 1..];
            let matched_participant = session
                .participants
                .iter()
                .filter_map(|participant| {
                    let name = participant.bot_name.as_deref()?;
                    if name.is_empty() || !after_at.starts_with(name) {
                        return None;
                    }
                    let remainder = &after_at[name.len()..];
                    if is_display_name_mention_boundary(remainder) {
                        Some((participant, name))
                    } else {
                        None
                    }
                })
                .max_by_key(|(_, name)| name.len());

            if let Some((participant, name)) = matched_participant {
                if !resolved_mentions.contains(&participant.bot_uuid) {
                    debug!(target: MSG_LOG_TARGET, mention = %name, resolved_to = %participant.bot_uuid, "routing full display-name mention match");
                    resolved_mentions.push(participant.bot_uuid.clone());
                }
                continue;
            }

            let at_mention = &message[at_index..];
            let Some(captures) = self.mention_regex.captures(at_mention) else {
                continue;
            };
            if captures.get(0).map_or(true, |value| value.start() != 0) {
                continue;
            }
            let Some(mention) = captures.get(1).map(|value| value.as_str()) else {
                continue;
            };
            let result = session.get_participant(mention).map(|p| p.bot_uuid.clone());
            debug!(target: MSG_LOG_TARGET, mention = %mention, matched = result.is_some(), resolved_to = ?result, "routing mention match");
            if let Some(bot_uuid) = result {
                if !resolved_mentions.contains(&bot_uuid) {
                    resolved_mentions.push(bot_uuid);
                }
            }
        }

        resolved_mentions
    }

    /// Check if message contains @ALL mention.
    fn has_all_mention(&self, message: &str) -> bool {
        let lower = message.to_lowercase();
        lower.contains("@all") || lower.contains("@所有人")
    }
}

impl Default for MessageRouter {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl RoutingCoreService for MessageRouter {
    async fn route(
        &self,
        session: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
    ) -> RoutingDecision {
        // Check for @ALL
        let has_all = self.has_all_mention(message);

        // Extract all @mentions from anywhere in the message.
        let mentions: Vec<&str> = self
            .mention_regex
            .captures_iter(message)
            .filter_map(|cap| cap.get(1).map(|m| m.as_str()))
            .collect();

        // Generate cleaned message with @ symbols removed (e.g. "@张三" → "张三")
        let cleaned_message = self.mention_regex.replace_all(message, "$1").to_string();

        let preview = truncate_preview(message, 50);
        debug!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            raw_mentions = ?mentions,
            has_all = has_all,
            msg_preview = %preview,
            "routing mention parse"
        );

        // Log participant names for mention matching diagnostics
        let participant_names: Vec<_> = session
            .participants
            .iter()
            .map(|p| (&p.bot_uuid, &p.bot_name))
            .collect();
        debug!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            participants = ?participant_names,
            "routing mention candidates"
        );

        // Validate mentions against session participants (match by bot_uuid OR bot_name)
        // Resolve to bot_uuid so downstream logic is consistent
        let valid_mentions = self.resolve_mentions(session, message);

        // Build routing targets with delivery type
        // Rule: Exclude sender from targets, only route to Bot participants
        let targets: Vec<_> = session
            .participants
            .iter()
            .filter(|participant| {
                // Only route to bot participants
                participant.is_bot()
            })
            .filter(|participant| {
                // Exclude sender
                sender_bot_id.map_or(true, |s| participant.bot_uuid != s)
            })
            // ManagerWorker: workers are fully excluded from broadcast —
            // even @mentions don't reach them. Workers only receive via
            // bcs_assign_task task dispatch.
            .filter(|p| {
                if session.group_strategy == GroupStrategy::ManagerWorker
                    && !is_lead_participant(session.group_strategy, p)
                {
                    return false;
                }
                true
            })
            .map(|participant| {
                let is_driver = is_lead_participant(session.group_strategy, participant);
                let is_mentioned = valid_mentions.contains(&participant.bot_uuid);

                // Determine delivery type
                let delivery_type = if has_all {
                    // @ALL → everyone gets Send
                    DeliveryType::Send
                } else if !valid_mentions.is_empty() {
                    // Has @mentions → mentioned get Send, others get Inject
                    if is_mentioned {
                        DeliveryType::Send
                    } else {
                        DeliveryType::Inject
                    }
                } else {
                    // No @mention → coordinator gets Send, others get Inject
                    if is_driver {
                        DeliveryType::Send
                    } else {
                        DeliveryType::Inject
                    }
                };

                RoutingTarget {
                    bot_uuid: participant.bot_uuid.clone(),
                    url: String::new(), // WebSocket delivery, no HTTP URL needed
                    is_driver,
                    delivery_type,
                }
            })
            .collect();

        info!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            from = ?sender_bot_id,
            phase = "route",
            route_source = "mention",
            targets = ?targets.iter().map(|t| format!("{}:{}", t.bot_uuid, if t.delivery_type == DeliveryType::Send { "send" } else { "inject" })).collect::<Vec<_>>(),
            mentions = ?valid_mentions,
            "routing decision [mention]"
        );

        RoutingDecision {
            targets,
            mentions: valid_mentions,
            cleaned_message,
            hidden_mentions: vec![],
        }
    }

    async fn route_structured(
        &self,
        group: &Group,
        routing: &ChatEventRouting,
        sender_bot_id: &str,
        registry: &dyn BotRegistryCoreService,
    ) -> Result<RoutingDecision, StructuredRoutingError> {
        // Convert wire selectors to internal representation
        let selectors: Vec<RouteSelector> = routing
            .responders
            .iter()
            .map(|wire| {
                RouteSelector::try_from(wire)
                    .map_err(|e| StructuredRoutingError::InvalidSelector(e.to_string()))
            })
            .collect::<Result<Vec<_>, _>>()?;

        // Resolve each selector to a set of bot_uuids (OR/union semantics)
        let include_self = routing.include_self.unwrap_or(false);
        let mut resolved: HashSet<String> = HashSet::new();

        for selector in &selectors {
            match selector {
                RouteSelector::Bot { bot_uuid } => {
                    if group.get_participant(bot_uuid).is_none() {
                        return Err(StructuredRoutingError::TargetNotInGroup(bot_uuid.clone()));
                    }
                    resolved.insert(bot_uuid.clone());
                }
                RouteSelector::Name { bot_name } => {
                    let matches: Vec<_> = group
                        .participants
                        .iter()
                        .filter(|p| {
                            p.bot_name
                                .as_deref()
                                .map_or(false, |name| name == bot_name.as_str())
                        })
                        .collect();
                    if matches.len() > 1 {
                        return Err(StructuredRoutingError::AmbiguousTarget(bot_name.clone()));
                    }
                    if let Some(p) = matches.first() {
                        resolved.insert(p.bot_uuid.clone());
                    }
                }
                RouteSelector::Role { role } => {
                    let role_enum = match role.as_str() {
                        "driver" => Some(ParticipantRole::Driver),
                        "consultant" => Some(ParticipantRole::Consultant),
                        "observer" => Some(ParticipantRole::Observer),
                        _ => None,
                    };
                    if let Some(r) = role_enum {
                        for p in &group.participants {
                            if p.role == r {
                                resolved.insert(p.bot_uuid.clone());
                            }
                        }
                    } else {
                        // Fallback: LLM may confuse role with name — try name match
                        for p in &group.participants {
                            if p.bot_name
                                .as_deref()
                                .map_or(false, |name| name.eq_ignore_ascii_case(role))
                            {
                                resolved.insert(p.bot_uuid.clone());
                            }
                        }
                    }
                }
                RouteSelector::Capability { value } => {
                    for p in &group.participants {
                        if let Some(bot) = registry.get(&p.bot_uuid).await {
                            if bot.capabilities.skills.iter().any(|s| s.name == *value)
                                || bot.capabilities.domains.contains(value)
                            {
                                resolved.insert(p.bot_uuid.clone());
                            }
                        }
                    }
                }
                RouteSelector::ParticipantsAll => {
                    for p in &group.participants {
                        if include_self || p.bot_uuid != sender_bot_id {
                            resolved.insert(p.bot_uuid.clone());
                        }
                    }
                }
                RouteSelector::ParticipantsOthers => {
                    for p in &group.participants {
                        if p.bot_uuid != sender_bot_id {
                            resolved.insert(p.bot_uuid.clone());
                        }
                    }
                }
                RouteSelector::Originator => {
                    resolved.insert(group.originator().to_string());
                }
                RouteSelector::Driver => {
                    let lead = group
                        .participants
                        .iter()
                        .find(|p| p.role == group.group_strategy.lead_role())
                        .map(|p| p.bot_uuid.clone())
                        .unwrap_or_else(|| group.driver_bot.clone());
                    resolved.insert(lead);
                }
            }
        }

        // No match → error
        if resolved.is_empty() {
            return Err(StructuredRoutingError::NoTargetMatched);
        }

        info!(
            target: MSG_LOG_TARGET,
            group_id = %group.id,
            from = %sender_bot_id,
            phase = "route",
            route_source = "structured",
            resolved = ?resolved,
            "routing decision [structured]"
        );

        // Build targets: resolved bots get Send, others get Inject
        // Sender is excluded from targets unless include_self is true
        let targets: Vec<RoutingTarget> = group
            .participants
            .iter()
            .filter(|p| p.is_bot())
            .filter(|p| {
                if p.bot_uuid == sender_bot_id {
                    include_self && resolved.contains(&p.bot_uuid)
                } else {
                    true
                }
            })
            .map(|p| RoutingTarget {
                bot_uuid: p.bot_uuid.clone(),
                url: String::new(),
                is_driver: is_lead_participant(group.group_strategy, p),
                delivery_type: if resolved.contains(&p.bot_uuid) {
                    DeliveryType::Send
                } else {
                    DeliveryType::Inject
                },
            })
            .collect();

        // Populate mentions for backward compatibility (bots that get Send)
        let mentions: Vec<String> = resolved.into_iter().collect();

        Ok(RoutingDecision {
            targets,
            mentions,
            cleaned_message: String::new(),
            hidden_mentions: vec![],
        })
    }

    async fn send_to_bot(
        &self,
        _target: &RoutingTarget,
        _message: &str,
        _from: Option<&str>,
        _group_id: Option<&str>,
    ) -> BotSendResult {
        // Bot communication is now handled via WebSocket only.
        // This method returns an error indicating WebSocket routing is required.
        warn!(
            "HTTP bot routing is deprecated. All bot communication must go through WebSocket. \
             Use the WebSocket connection manager to send messages to bots."
        );

        BotSendResult {
            bot_uuid: _target.bot_uuid.clone(),
            content: String::new(),
            success: false,
            error: Some("HTTP bot routing deprecated - use WebSocket".to_string()),
        }
    }

    async fn route_and_send(
        &self,
        session: &Group,
        message: &str,
        from: Option<&str>,
    ) -> RouteAndSendResult {
        // 'from' is the sender_bot_id
        let decision = self.route(session, message, from).await;

        let mut results = Vec::new();
        for target in &decision.targets {
            let result = self
                .send_to_bot(target, message, from, Some(&session.id))
                .await;
            results.push(result);
        }

        RouteAndSendResult {
            results,
            mentions: decision.mentions,
        }
    }

    /// X.3 + CR-2: route a message with mode/status overlay applied per
    /// design §4.5. This is a **self-contained router**, NOT a post-pass
    /// on the legacy `route()` output.
    ///
    /// Why self-contained (CR-2 fix):
    /// the previous version delegated to `self.route(...)` and then
    /// post-processed the decision. But `route()` only ever puts **Bot**
    /// participants into `targets`, so any `@Human` mention disappeared
    /// from the target set. The legacy "has any valid mention → mention
    /// branch" logic then concluded that nobody was mentioned among the
    /// targets, demoted every Bot (including the driver) to Inject, and
    /// no one received a `Send`. The overlay post-pass had no chance to
    /// reconstruct the driver's Send. With this rewrite the router knows
    /// up-front which mentions resolved to Humans vs Bots and routes
    /// accordingly.
    ///
    /// Routing rules (in order):
    ///
    /// 1. **Sender exclusion**: sender never appears in targets.
    /// 2. **Human absent overlay**: a Human participant whose
    ///    `mode=absent` is "not in the room". If the message
    ///    @-mentions an absent Human the mention is silently dropped
    ///    *before* the delivery-type decision (so an `@absent_human`
    ///    plus driver behaves identically to an unmentioned message).
    /// 3. **Delivery-type decision** for each Bot target:
    ///    - `@ALL` / `@所有人` present → Send to every Bot target
    ///    - else **at least one valid Bot mention survives** → only the
    ///      mentioned Bot(s) get Send, others get Inject
    ///    - else (no mention OR only Human mentions survive) → driver
    ///      gets Send, other Bots get Inject
    /// 4. **Forced-Inject overlay** (after step 3):
    ///    - `mode=muted` Bot → Send downgraded to Inject (still observes,
    ///      never auto-replies)
    ///    - `status=hidden` Bot → Send downgraded to Inject (X.7 layers
    ///      `silent=true` on the WS event)
    ///
    /// `mentions` returned to callers includes **all** valid mentions
    /// (Bot uuids + present Human uuids); absent Humans are dropped.
    /// This preserves the contract that downstream consumers can list
    /// who was @-targeted, while keeping the `targets` table strictly
    /// receiver-side (Bot dispatch only).
    ///
    /// Note: the sender-side mode/status gate (X.1/X.2) is the caller's
    /// responsibility at WS ingress; this method only handles receiver-side
    /// fan-out semantics.
    async fn route_with_overlay(
        &self,
        session: &Group,
        message: &str,
        sender_bot_id: Option<&str>,
        overlay: &[RouteParticipantOverlay],
    ) -> RoutingDecision {
        // Build quick lookups.
        let overlay_map: HashMap<&str, &RouteParticipantOverlay> =
            overlay.iter().map(|o| (o.bot_uuid.as_str(), o)).collect();

        // Step 0: parse mentions and @ALL marker (mirrors `route()`).
        let has_all = self.has_all_mention(message);
        let raw_mentions: Vec<&str> = self
            .mention_regex
            .captures_iter(message)
            .filter_map(|cap| cap.get(1).map(|m| m.as_str()))
            .collect();
        let cleaned_message = self.mention_regex.replace_all(message, "$1").to_string();

        let preview = truncate_preview(message, 50);
        debug!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            raw_mentions = ?raw_mentions,
            has_all = has_all,
            msg_preview = %preview,
            "routing mention parse [overlay]"
        );

        // Step 1: resolve mentions to bot_uuids using session participants
        // (matches by bot_uuid OR bot_name, identical to legacy `route()`).
        let resolved_mentions = self.resolve_mentions(session, message);

        // Step 2: classify mentions into Bot vs Human, dropping absent Humans.
        // We need both:
        //   - `bot_mentions`: drives the delivery-type decision below
        //   - `surviving_mentions`: returned to callers in `decision.mentions`
        let mut bot_mentions: HashSet<&str> = HashSet::new();
        let mut surviving_mentions: Vec<String> = Vec::new();
        let mut dropped_absent: Vec<String> = Vec::new();
        let mut hidden_mentions: Vec<HiddenMentionInfo> = Vec::new();

        for uuid in &resolved_mentions {
            // Try the overlay first (carries authoritative actor_kind/mode).
            // Fall back to the participant row's actor_kind if overlay is
            // missing — a defensive default so partial overlays don't
            // break routing.
            let (actor_kind, mode) = match overlay_map.get(uuid.as_str()) {
                Some(o) => (o.actor_kind, overlay_effective_mode(o)),
                None => {
                    let p = session.get_participant(uuid);
                    let kind = p.map(|p| p.actor_kind).unwrap_or(ActorKind::Bot);
                    let mode = p
                        .and_then(|p| p.mode)
                        .unwrap_or_else(|| ParticipantMode::default_for(kind));
                    (kind, mode)
                }
            };

            match actor_kind {
                ActorKind::Bot => {
                    let is_hidden = overlay_map
                        .get(uuid.as_str())
                        .map_or(false, |o| o.status == ActorStatus::Hidden);
                    if is_hidden {
                        let bot_name = overlay_map
                            .get(uuid.as_str())
                            .and_then(|o| o.bot_name.clone())
                            .unwrap_or_else(|| uuid.clone());
                        hidden_mentions.push(HiddenMentionInfo {
                            hidden_bot_id: uuid.clone(),
                            hidden_bot_name: bot_name,
                        });
                    } else {
                        bot_mentions.insert(uuid.as_str());
                        surviving_mentions.push(uuid.clone());
                    }
                }
                ActorKind::Human => {
                    if mode == ParticipantMode::Absent {
                        // Absent Human: drop entirely — they aren't even on
                        // the routing table, so the @-mention should not
                        // influence delivery-type either.
                        dropped_absent.push(uuid.clone());
                    } else {
                        // Present Human: surface in `mentions` for transcript
                        // and frontend ack, but they don't enter `targets`
                        // (targets is Bot-only) and they don't trigger the
                        // mention-branch below (no Bot was @-targeted).
                        surviving_mentions.push(uuid.clone());
                    }
                }
            }
        }

        // Step 3: build Bot targets with delivery_type per the rules above.
        //
        // ManagerWorker groups: workers are completely excluded from the
        // broadcast table — even @mentions don't reach them. Workers only
        // receive via bcs_assign_task task dispatch.
        let targets: Vec<RoutingTarget> = session
            .participants
            .iter()
            .filter(|p| p.is_bot())
            .filter(|p| sender_bot_id.map_or(true, |s| p.bot_uuid != s))
            .filter(|p| {
                if session.group_strategy == GroupStrategy::ManagerWorker
                    && !is_lead_participant(session.group_strategy, p)
                {
                    return false;
                }
                true
            })
            .map(|p| {
                let is_driver = is_lead_participant(session.group_strategy, p);
                let is_mentioned = bot_mentions.contains(p.bot_uuid.as_str());

                let mut delivery_type = if has_all {
                    DeliveryType::Send
                } else if !bot_mentions.is_empty() {
                    // Bot mention branch: only mentioned Bots get Send.
                    if is_mentioned {
                        DeliveryType::Send
                    } else {
                        DeliveryType::Inject
                    }
                } else {
                    // No Bot mention (either no mention at all OR only
                    // Human mentions survived): driver gets Send.
                    if is_driver {
                        DeliveryType::Send
                    } else {
                        DeliveryType::Inject
                    }
                };

                // Step 4: forced-Inject overlay for muted/hidden Bots.
                if delivery_type == DeliveryType::Send {
                    if let Some(o) = overlay_map.get(p.bot_uuid.as_str()) {
                        if overlay_forced_inject(o) {
                            delivery_type = DeliveryType::Inject;
                        }
                    }
                }

                RoutingTarget {
                    bot_uuid: p.bot_uuid.clone(),
                    url: String::new(),
                    is_driver,
                    delivery_type,
                }
            })
            .collect();

        info!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            from = ?sender_bot_id,
            phase = "route",
            route_source = "overlay",
            has_all = has_all,
            bot_mentions = ?bot_mentions,
            dropped_absent_humans = ?dropped_absent,
            targets = ?targets.iter().map(|t| format!("{}:{}", t.bot_uuid, if t.delivery_type == DeliveryType::Send { "send" } else { "inject" })).collect::<Vec<_>>(),
            mentions = ?surviving_mentions,
            "routing decision [overlay]"
        );

        RoutingDecision {
            targets,
            mentions: surviving_mentions,
            cleaned_message,
            hidden_mentions,
        }
    }

    async fn route_dm_with_overlay(
        &self,
        session: &Group,
        message: &str,
        sender_actor_id: &str,
        overlay: &[RouteParticipantOverlay],
    ) -> RoutingDecision {
        if session.group_kind != GroupKind::Dm {
            return self
                .route_with_overlay(session, message, Some(sender_actor_id), overlay)
                .await;
        }

        let overlay_map: HashMap<&str, &RouteParticipantOverlay> =
            overlay.iter().map(|o| (o.bot_uuid.as_str(), o)).collect();
        let raw_mentions: Vec<&str> = self
            .mention_regex
            .captures_iter(message)
            .filter_map(|cap| cap.get(1).map(|m| m.as_str()))
            .collect();
        let cleaned_message = self.mention_regex.replace_all(message, "$1").to_string();
        let mentions: Vec<String> = raw_mentions
            .into_iter()
            .filter_map(|m| session.get_participant(m).map(|p| p.bot_uuid.clone()))
            .filter(|uuid| {
                overlay_map.get(uuid.as_str()).map_or(true, |row| {
                    row.actor_kind != ActorKind::Human
                        || overlay_effective_mode(row) != ParticipantMode::Absent
                })
            })
            .collect();

        let sender_in_group = session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == sender_actor_id);
        if !sender_in_group {
            warn!(
                target: MSG_LOG_TARGET,
                group_id = %session.id,
                sender = %sender_actor_id,
                "dm routing skipped: sender is not a participant"
            );
            return RoutingDecision {
                targets: Vec::new(),
                mentions,
                cleaned_message,
                hidden_mentions: vec![],
            };
        }

        debug_assert_eq!(
            session.participants.len(),
            2,
            "DM group has unexpected participant count"
        );

        let targets = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid != sender_actor_id)
            .filter(|participant| participant.is_bot())
            .map(|participant| {
                let mut delivery_type = DeliveryType::Send;
                if let Some(row) = overlay_map.get(participant.bot_uuid.as_str()) {
                    if overlay_forced_inject(row) {
                        delivery_type = DeliveryType::Inject;
                    }
                }

                RoutingTarget {
                    bot_uuid: participant.bot_uuid.clone(),
                    url: String::new(),
                    is_driver: is_lead_participant(session.group_strategy, participant),
                    delivery_type,
                }
            })
            .into_iter()
            .collect::<Vec<_>>();

        info!(
            target: MSG_LOG_TARGET,
            group_id = %session.id,
            from = %sender_actor_id,
            phase = "route",
            route_source = "dm",
            targets = ?targets.iter().map(|t| format!("{}:{}", t.bot_uuid, if t.delivery_type == DeliveryType::Send { "send" } else { "inject" })).collect::<Vec<_>>(),
            mentions = ?mentions,
            "routing decision [dm]"
        );

        RoutingDecision {
            targets,
            mentions,
            cleaned_message,
            hidden_mentions: vec![],
        }
    }
}

/// Build a RoutingDecision based on sender_routes configuration.
///
/// Targets in `target_ids` receive `DeliveryType::Send`, all other participants
/// receive `DeliveryType::Inject`. The sender is always excluded.
/// Targets not in the group are skipped with a warning log.
pub fn build_sender_route_decision(
    session: &Group,
    sender_bot_id: &str,
    target_ids: &[String],
) -> RoutingDecision {
    let participant_set: HashSet<&str> = session
        .participants
        .iter()
        .map(|p| p.bot_uuid.as_str())
        .collect();

    // Filter targets to only those in the group
    let valid_targets: HashSet<&str> = target_ids
        .iter()
        .filter(|t| {
            if !participant_set.contains(t.as_str()) {
                warn!(
                    target: MSG_LOG_TARGET,
                    group_id = %session.id,
                    phase = "route",
                    route_target = %t,
                    "sender_route target not in group, skipping"
                );
                false
            } else {
                true
            }
        })
        .map(|t| t.as_str())
        .collect();

    let targets: Vec<RoutingTarget> = session
        .participants
        .iter()
        .filter(|p| p.is_bot() && p.bot_uuid != sender_bot_id)
        .map(|p| RoutingTarget {
            bot_uuid: p.bot_uuid.clone(),
            url: String::new(),
            is_driver: is_lead_participant(session.group_strategy, p),
            delivery_type: if valid_targets.contains(p.bot_uuid.as_str()) {
                DeliveryType::Send
            } else {
                DeliveryType::Inject
            },
        })
        .collect();

    let mentions: Vec<String> = valid_targets.iter().map(|s| s.to_string()).collect();

    RoutingDecision {
        targets,
        mentions,
        cleaned_message: String::new(),
        hidden_mentions: vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::{
        BotCapabilities, BotDynamicStatus, ChatEventRouting, GroupStrategy, Participant,
        ParticipantRole, RegisteredBot, ResponseMode, RouteSelectorWire, Workspace,
    };
    use bcs_test_support::NoopBotRegistryCoreService;

    fn create_test_session() -> Group {
        Group {
            id: "test-session".to_string(),
            label: None,
            status: bcs_service_api::GroupStatus::Active,
            driver_bot: "driver".to_string(),
            originator: None, // Defaults to driver_bot
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "driver".to_string(),
                    bot_name: None,
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "consultant".to_string(),
                    bot_name: None,
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }

    #[tokio::test]
    async fn test_broadcast_to_all_on_no_mention() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router
            .route(&session, "Hello, can you help me?", None)
            .await;

        assert!(decision.mentions.is_empty());
        assert_eq!(decision.cleaned_message, "Hello, can you help me?");
        assert_eq!(decision.targets.len(), 2);

        let driver_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "driver")
            .unwrap();
        let consultant_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "consultant")
            .unwrap();
        assert_eq!(driver_target.delivery_type, DeliveryType::Send);
        assert_eq!(consultant_target.delivery_type, DeliveryType::Inject);
    }

    #[tokio::test]
    async fn test_mentioned_bot_gets_send_others_get_inject() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router
            .route(&session, "@consultant what do you think?", None)
            .await;

        assert_eq!(decision.mentions, vec!["consultant"]);
        assert_eq!(decision.cleaned_message, "consultant what do you think?");
        assert_eq!(decision.targets.len(), 2);

        let driver_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "driver")
            .unwrap();
        let consultant_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "consultant")
            .unwrap();
        assert_eq!(driver_target.delivery_type, DeliveryType::Inject);
        assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
    }

    #[tokio::test]
    async fn test_all_mention_everyone_gets_send() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router.route(&session, "@all please respond", None).await;

        assert_eq!(decision.targets.len(), 2);
        // Everyone should get Send
        for target in &decision.targets {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        }
    }

    #[tokio::test]
    async fn test_sender_is_excluded() {
        let router = MessageRouter::new();
        let session = create_test_session();

        // Sender is driver, should be excluded from targets
        let decision = router.route(&session, "Hello", Some("driver")).await;

        assert_eq!(decision.targets.len(), 1); // Only consultant
        assert_eq!(decision.targets[0].bot_uuid, "consultant");

        // No mentions, so consultant (non-driver) gets Inject
        assert_eq!(decision.targets[0].delivery_type, DeliveryType::Inject);
    }

    #[tokio::test]
    async fn test_multiple_mentions() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router
            .route(&session, "@driver @consultant please coordinate", None)
            .await;

        assert_eq!(decision.mentions.len(), 2);
        assert!(decision.mentions.contains(&"driver".to_string()));
        assert!(decision.mentions.contains(&"consultant".to_string()));
        assert_eq!(
            decision.cleaned_message,
            "driver consultant please coordinate"
        );
        assert_eq!(decision.targets.len(), 2);

        // Both mentioned, both get Send
        for target in &decision.targets {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        }
    }

    #[tokio::test]
    async fn test_repeated_full_display_name_mentions_all_get_send() {
        let router = MessageRouter::new();
        let mut session = create_overlay_test_session();
        session.participants[0].bot_name = Some("alpha-甲".to_string());
        session.participants[1].bot_name = Some("beta (乙)".to_string());
        session.participants[2].bot_name = Some("beta".to_string());
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let decision = router
            .route_with_overlay(
                &session,
                "@alpha-甲 @beta (乙) coordinate; @alpha-甲 and @beta (乙) confirm",
                Some("sender"),
                &overlay,
            )
            .await;

        assert_eq!(
            decision.mentions,
            vec!["driver".to_string(), "bot_x".to_string()]
        );
        assert_eq!(
            decision.cleaned_message,
            "alpha-甲 beta (乙) coordinate; alpha-甲 and beta (乙) confirm"
        );
        assert_eq!(decision.targets.len(), 2);
        for target in &decision.targets {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        }
    }

    #[tokio::test]
    async fn test_invalid_mention_ignored() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router.route(&session, "@unknown hello", None).await;

        assert!(decision.mentions.is_empty()); // Invalid mention ignored
        assert_eq!(decision.cleaned_message, "unknown hello");
        assert_eq!(decision.targets.len(), 2);

        // No valid mentions, driver gets Send, consultant gets Inject
        let driver_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "driver")
            .unwrap();
        let consultant_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "consultant")
            .unwrap();
        assert_eq!(driver_target.delivery_type, DeliveryType::Send);
        assert_eq!(consultant_target.delivery_type, DeliveryType::Inject);
    }

    #[tokio::test]
    async fn test_send_to_bot_returns_deprecation_error() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router.route(&session, "Hello", None).await;
        let result = router
            .send_to_bot(&decision.targets[0], "test", None, Some("test-session"))
            .await;

        assert!(!result.success);
        assert!(result.error.unwrap().contains("deprecated"));
    }

    #[tokio::test]
    async fn test_mid_message_mention_recognized() {
        let router = MessageRouter::new();
        let session = create_test_session();

        // @mention in the middle — should now be recognized as a routing directive
        let decision = router
            .route(&session, "请在你的回复中 @consultant 并请他介绍自己", None)
            .await;

        assert_eq!(decision.mentions, vec!["consultant"]);
        assert_eq!(
            decision.cleaned_message,
            "请在你的回复中 consultant 并请他介绍自己"
        );
        let driver_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "driver")
            .unwrap();
        let consultant_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "consultant")
            .unwrap();
        assert_eq!(driver_target.delivery_type, DeliveryType::Inject);
        assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
    }

    #[tokio::test]
    async fn test_leading_mention_routes_correctly() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router.route(&session, "@consultant 请介绍自己", None).await;

        assert_eq!(decision.mentions, vec!["consultant"]);
        assert_eq!(decision.cleaned_message, "consultant 请介绍自己");
        let consultant_target = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "consultant")
            .unwrap();
        assert_eq!(consultant_target.delivery_type, DeliveryType::Send);
    }

    #[tokio::test]
    async fn test_scattered_mentions_in_text() {
        let router = MessageRouter::new();
        let session = create_test_session();

        let decision = router
            .route(&session, "请 @driver 和 @consultant 一起讨论", None)
            .await;

        assert_eq!(decision.mentions.len(), 2);
        assert!(decision.mentions.contains(&"driver".to_string()));
        assert!(decision.mentions.contains(&"consultant".to_string()));
        assert_eq!(decision.cleaned_message, "请 driver 和 consultant 一起讨论");

        for target in &decision.targets {
            assert_eq!(target.delivery_type, DeliveryType::Send);
        }
    }

    #[tokio::test]
    async fn test_chinese_name_mentions() {
        let router = MessageRouter::new();
        let session = Group {
            id: "test-session".to_string(),
            label: None,
            status: bcs_service_api::GroupStatus::Active,
            driver_bot: "pmo".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "pmo".to_string(),
                    bot_name: Some("PMO".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "xiongbing".to_string(),
                    bot_name: Some("Developer Bot".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "xiahong".to_string(),
                    bot_name: Some("QA Bot".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        };

        let decision = router
            .route(
                &session,
                "请 @Developer Bot 进行技术评估，@QA Bot 进行质量评估。",
                Some("pmo"),
            )
            .await;

        assert_eq!(decision.mentions.len(), 2);
        assert!(decision.mentions.contains(&"xiongbing".to_string()));
        assert!(decision.mentions.contains(&"xiahong".to_string()));
        assert_eq!(
            decision.cleaned_message,
            "请 Developer Bot 进行技术评估，QA Bot 进行质量评估。"
        );

        // Both mentioned bots get Send
        let xb = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "xiongbing")
            .unwrap();
        let xh = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "xiahong")
            .unwrap();
        assert_eq!(xb.delivery_type, DeliveryType::Send);
        assert_eq!(xh.delivery_type, DeliveryType::Send);
    }

    // =========================================================================
    // Structured Routing Tests (Tasks 11.x)
    // =========================================================================

    /// Simple registry that returns pre-configured bots for capability lookups.
    struct TestBotRegistry {
        bots: std::collections::HashMap<String, RegisteredBot>,
    }

    impl TestBotRegistry {
        fn new() -> Self {
            Self {
                bots: std::collections::HashMap::new(),
            }
        }
        fn add(&mut self, id: &str, skills: Vec<&str>, domains: Vec<&str>) {
            self.bots.insert(
                id.to_string(),
                RegisteredBot {
                    bot_uuid: id.to_string(),
                    capabilities: BotCapabilities {
                        name: Some(id.to_string()),
                        summary: None,
                        skills: skills
                            .into_iter()
                            .map(bcs_service_api::Skill::new)
                            .collect(),
                        domains: domains.into_iter().map(|s| s.to_string()).collect(),
                        scopes: vec![],
                        binding_channels: None,
                        hidden: false,
                        visibility: "protected".to_string(),
                        agent_code: None,
                        agent_token: None,
                    },
                    dynamic_status: BotDynamicStatus::default(),
                    env: None,
                    created_by: None,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    status: bcs_service_api::ActorStatus::default(),
                },
            );
        }
    }

    #[async_trait]
    impl bcs_service_api::BotRegistryCoreService for TestBotRegistry {
        async fn register(
            &self,
            _: String,
            _: BotCapabilities,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn update_status(&self, _: &str, _: BotDynamicStatus) -> bool {
            false
        }
        async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
            self.bots.get(bot_id).cloned()
        }
        async fn get_agent_credentials(
            &self,
            _bot_id: &str,
        ) -> Option<bcs_service_api::AgentCredentials> {
            None
        }
        async fn list_active(&self) -> Vec<RegisteredBot> {
            self.bots.values().cloned().collect()
        }
        async fn discover(&self, _: &str) -> Vec<RegisteredBot> {
            vec![]
        }
        async fn find_by_skills(&self, _: &[&str]) -> Vec<RegisteredBot> {
            vec![]
        }
        async fn find_by_domains(&self, _: &[&str]) -> Vec<RegisteredBot> {
            vec![]
        }
        async fn find_by_scopes(&self, _: &[&str]) -> Vec<RegisteredBot> {
            vec![]
        }
        async fn unregister(&self, _: &str) -> bool {
            false
        }
        async fn cleanup_expired(&self) {}
        async fn load_from_storage(&self, _: &str) -> Option<BotCapabilities> {
            None
        }
        async fn save_to_storage(
            &self,
            _: &str,
            _: &BotCapabilities,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn update_visibility(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        #[allow(deprecated)]
        async fn set_hidden(&self, _: &str, _: bool) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn has_been_onboarded(&self, _: &str) -> bool {
            false
        }
        async fn save_token(&self, _: &str, _: &str) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn load_token(&self, _: &str) -> Option<String> {
            None
        }
        async fn find_bot_by_token(&self, _: &str) -> Option<String> {
            None
        }
        async fn register_streaming_connection(&self, _: String) -> Result<String, ()> {
            Err(())
        }
        async fn reconnect_streaming(&self, _: String) -> Result<(String, String), ()> {
            Err(())
        }
        async fn disconnect_streaming(&self, _: &str) {}
        async fn is_connected(&self, _: &str) -> bool {
            false
        }
        async fn send_frame(&self, _: &str, _: String) -> Result<(), ()> {
            Err(())
        }
        async fn list_connected(&self) -> Vec<String> {
            vec![]
        }
        async fn store_token_mapping(&self, _: String, _: String) {}
        async fn register_http_connection(&self, _: String, token: String) -> String {
            token
        }
        async fn save_created_by(
            &self,
            _bot_id: &str,
            _created_by: &str,
            _overwrite: bool,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
            vec![]
        }
    }

    fn create_structured_test_session() -> Group {
        Group {
            id: "grp-001".to_string(),
            label: None,
            status: bcs_service_api::GroupStatus::Active,
            driver_bot: "alice".to_string(),
            originator: Some("alice".to_string()),
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "alice".to_string(),
                    bot_name: Some("Alice".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "bob".to_string(),
                    bot_name: Some("Bob".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "carol".to_string(),
                    bot_name: Some("Carol".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }

    // 11.1: RouteSelectorWire → RouteSelector conversion
    #[test]
    fn test_wire_to_selector_bot() {
        let wire = RouteSelectorWire {
            selector_type: "bot".to_string(),
            value: Some("abc".to_string()),
        };
        let sel = RouteSelector::try_from(&wire).unwrap();
        assert_eq!(
            sel,
            RouteSelector::Bot {
                bot_uuid: "abc".to_string()
            }
        );
    }

    #[test]
    fn test_wire_to_selector_capability() {
        let wire = RouteSelectorWire {
            selector_type: "capability".to_string(),
            value: Some("database".to_string()),
        };
        let sel = RouteSelector::try_from(&wire).unwrap();
        assert_eq!(
            sel,
            RouteSelector::Capability {
                value: "database".to_string()
            }
        );
    }

    #[test]
    fn test_wire_to_selector_participants_all() {
        let wire = RouteSelectorWire {
            selector_type: "participants".to_string(),
            value: Some("all".to_string()),
        };
        let sel = RouteSelector::try_from(&wire).unwrap();
        assert_eq!(sel, RouteSelector::ParticipantsAll);
    }

    #[test]
    fn test_wire_to_selector_unknown_type_errors() {
        let wire = RouteSelectorWire {
            selector_type: "unknown".to_string(),
            value: None,
        };
        let err = RouteSelector::try_from(&wire).unwrap_err();
        assert!(matches!(err, RouteSelectorError::UnsupportedType(_)));
    }

    #[test]
    fn test_wire_to_selector_missing_value_errors() {
        let wire = RouteSelectorWire {
            selector_type: "bot".to_string(),
            value: None,
        };
        let err = RouteSelector::try_from(&wire).unwrap_err();
        assert!(matches!(err, RouteSelectorError::MissingValue(_)));
    }

    // 11.2: Capability exact tag matching
    #[tokio::test]
    async fn test_capability_exact_match() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let mut registry = TestBotRegistry::new();
        registry.add("alice", vec![], vec![]);
        registry.add("bob", vec!["sql_analysis"], vec!["database"]);
        registry.add("carol", vec!["code_review"], vec!["security"]);

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "capability".to_string(),
                value: Some("database".to_string()),
            }],
            mode: Some(ResponseMode::Required),
            reason: "need db expert".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let decision = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap();
        // Bob has "database" domain → Send; Alice (sender) excluded; Carol → Inject
        let bob = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "bob")
            .unwrap();
        let carol = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "carol")
            .unwrap();
        assert_eq!(bob.delivery_type, DeliveryType::Send);
        assert_eq!(carol.delivery_type, DeliveryType::Inject);
        assert!(decision.mentions.contains(&"bob".to_string()));
    }

    // 11.3: participants:all default excludes sender, include_self=true includes sender
    #[tokio::test]
    async fn test_participants_all_excludes_sender() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "participants".to_string(),
                value: Some("all".to_string()),
            }],
            mode: None,
            reason: "broadcast".to_string(),
            include_self: None, // default false
            dedupe_key: None,
        };

        let decision = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap();
        // Alice (sender) excluded, Bob and Carol get Send
        assert_eq!(decision.targets.len(), 2);
        assert!(decision.targets.iter().all(|t| t.bot_uuid != "alice"));
        assert!(
            decision
                .targets
                .iter()
                .all(|t| t.delivery_type == DeliveryType::Send)
        );
    }

    #[tokio::test]
    async fn test_participants_all_include_self() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "participants".to_string(),
                value: Some("all".to_string()),
            }],
            mode: None,
            reason: "broadcast including self".to_string(),
            include_self: Some(true),
            dedupe_key: None,
        };

        let decision = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap();
        // All 3 participants get Send (including sender)
        assert_eq!(decision.targets.len(), 3);
        let alice = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "alice")
            .unwrap();
        assert_eq!(alice.delivery_type, DeliveryType::Send);
    }

    // 11.4: Multiple selectors OR/union
    #[tokio::test]
    async fn test_multiple_selectors_union() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let mut registry = TestBotRegistry::new();
        registry.add("alice", vec![], vec![]);
        registry.add("bob", vec![], vec!["database"]);
        registry.add("carol", vec![], vec!["security"]);

        let routing = ChatEventRouting {
            responders: vec![
                RouteSelectorWire {
                    selector_type: "capability".to_string(),
                    value: Some("database".to_string()),
                },
                RouteSelectorWire {
                    selector_type: "capability".to_string(),
                    value: Some("security".to_string()),
                },
            ],
            mode: None,
            reason: "need both".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let decision = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap();
        // Both Bob (database) and Carol (security) get Send
        let bob = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "bob")
            .unwrap();
        let carol = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "carol")
            .unwrap();
        assert_eq!(bob.delivery_type, DeliveryType::Send);
        assert_eq!(carol.delivery_type, DeliveryType::Send);
    }

    // 11.5: Bot selector for non-participant returns TARGET_NOT_IN_GROUP
    #[tokio::test]
    async fn test_bot_not_in_group() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "bot".to_string(),
                value: Some("unknown_bot".to_string()),
            }],
            mode: None,
            reason: "test".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let err = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            bcs_service_api::StructuredRoutingError::TargetNotInGroup(_)
        ));
    }

    // 11.6: Name selector ambiguity
    #[tokio::test]
    async fn test_name_ambiguous() {
        let router = MessageRouter::new();
        let session = Group {
            id: "grp-002".to_string(),
            label: None,
            status: bcs_service_api::GroupStatus::Active,
            driver_bot: "a".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![
                Participant {
                    bot_uuid: "a".to_string(),
                    bot_name: Some("DBA".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
                Participant {
                    bot_uuid: "b".to_string(),
                    bot_name: Some("DBA".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: bcs_service_api::ActorKind::default(),
                    mode: None,
                },
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        };
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "name".to_string(),
                value: Some("DBA".to_string()),
            }],
            mode: None,
            reason: "test".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let err = router
            .route_structured(&session, &routing, "a", &registry)
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            bcs_service_api::StructuredRoutingError::AmbiguousTarget(_)
        ));
    }

    // 11.7: No target matched
    #[tokio::test]
    async fn test_no_target_matched() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        // Capability that nobody has (NoopRegistry returns None for all)
        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "capability".to_string(),
                value: Some("quantum_computing".to_string()),
            }],
            mode: None,
            reason: "test".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let err = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            bcs_service_api::StructuredRoutingError::NoTargetMatched
        ));
    }

    // 11.7b: Driver and Originator selectors
    #[tokio::test]
    async fn test_driver_selector() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "driver".to_string(),
                value: None,
            }],
            mode: None,
            reason: "ask driver".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        // Bob is sender, Alice is driver → Alice should get Send
        let decision = router
            .route_structured(&session, &routing, "bob", &registry)
            .await
            .unwrap();
        let alice = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "alice")
            .unwrap();
        assert_eq!(alice.delivery_type, DeliveryType::Send);
    }

    // 11.7c: Role selector
    #[tokio::test]
    async fn test_role_selector() {
        let router = MessageRouter::new();
        let session = create_structured_test_session();
        let registry = NoopBotRegistryCoreService;

        let routing = ChatEventRouting {
            responders: vec![RouteSelectorWire {
                selector_type: "role".to_string(),
                value: Some("consultant".to_string()),
            }],
            mode: None,
            reason: "ask consultants".to_string(),
            include_self: None,
            dedupe_key: None,
        };

        let decision = router
            .route_structured(&session, &routing, "alice", &registry)
            .await
            .unwrap();
        // Bob and Carol are consultants → Send; Alice (sender, driver) excluded
        assert_eq!(decision.targets.len(), 2);
        assert!(
            decision
                .targets
                .iter()
                .all(|t| t.delivery_type == DeliveryType::Send)
        );
    }

    // =========================================================================
    // Task 4.2: build_sender_route_decision tests
    // =========================================================================

    #[test]
    fn test_sender_route_all_targets_in_group() {
        let session = create_structured_test_session();
        let targets = vec!["bob".to_string(), "carol".to_string()];

        let decision = build_sender_route_decision(&session, "alice", &targets);

        // Sender (alice) excluded, bob and carol get Send
        assert_eq!(decision.targets.len(), 2);
        let bob = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "bob")
            .unwrap();
        let carol = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "carol")
            .unwrap();
        assert_eq!(bob.delivery_type, DeliveryType::Send);
        assert_eq!(carol.delivery_type, DeliveryType::Send);
    }

    #[test]
    fn test_sender_route_some_targets_not_in_group() {
        let session = create_structured_test_session();
        let targets = vec!["bob".to_string(), "missing_bot".to_string()];

        let decision = build_sender_route_decision(&session, "alice", &targets);

        // bob gets Send, carol gets Inject, missing_bot skipped
        let bob = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "bob")
            .unwrap();
        let carol = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "carol")
            .unwrap();
        assert_eq!(bob.delivery_type, DeliveryType::Send);
        assert_eq!(carol.delivery_type, DeliveryType::Inject);
        assert!(decision.targets.iter().all(|t| t.bot_uuid != "missing_bot"));
    }

    #[test]
    fn test_sender_route_empty_targets() {
        let session = create_structured_test_session();
        let targets: Vec<String> = vec![];

        let decision = build_sender_route_decision(&session, "alice", &targets);

        // All non-sender participants get Inject (no valid targets)
        assert_eq!(decision.targets.len(), 2);
        assert!(
            decision
                .targets
                .iter()
                .all(|t| t.delivery_type == DeliveryType::Inject)
        );
    }

    #[test]
    fn test_sender_route_sender_excluded() {
        let session = create_structured_test_session();
        let targets = vec!["alice".to_string(), "bob".to_string()];

        let decision = build_sender_route_decision(&session, "alice", &targets);

        // alice is sender, excluded from targets
        assert!(decision.targets.iter().all(|t| t.bot_uuid != "alice"));
        let bob = decision
            .targets
            .iter()
            .find(|t| t.bot_uuid == "bob")
            .unwrap();
        assert_eq!(bob.delivery_type, DeliveryType::Send);
    }

    // =========================================================================
    // I.6: route_with_overlay integration tests (Human Actor V1, X.3 + X.7)
    //
    // Covers:
    //   - mode × status overlay matrix (auto/muted × online/hidden) for Bots
    //   - @Human present vs absent behavior on the routing layer
    //   - hidden/muted forced-Inject downgrade
    //   - mentions list survives present-Human, drops absent-Human
    //
    // Notes:
    //   - X.7 envelope `silent=true` is added by `WorkbenchChannel::dispatch`,
    //     not by routing — these tests assert the routing-side contract only
    //     (delivery_type downgrade for hidden actors). The dispatch-side
    //     `silent` propagation is exercised in higher-level tests.
    //   - Sender-side gating (X.1/X.2) is at WS ingress, not routing's job;
    //     these tests therefore use sender_bot_id only to exclude the sender
    //     from `targets`, not to validate auth.
    // =========================================================================

    /// Build a 4-bot session for overlay tests:
    /// - "driver" (Bot, driver role)
    /// - "bot_x"  (Bot, consultant)
    /// - "human_alice" (Human)
    /// - "sender" (Bot, originator of the message)
    fn create_overlay_test_session() -> Group {
        Group {
            id: "ovr-001".to_string(),
            label: None,
            status: bcs_service_api::GroupStatus::Active,
            driver_bot: "driver".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            service_group_uuid: None,
            service_mode: None,
            participants: vec![
                Participant {
                    bot_uuid: "driver".to_string(),
                    bot_name: Some("Driver Bot".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
                Participant {
                    bot_uuid: "bot_x".to_string(),
                    bot_name: Some("Bot X".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
                Participant {
                    bot_uuid: "human_alice".to_string(),
                    bot_name: Some("Alice".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Human,
                    mode: Some(ParticipantMode::Present),
                },
                Participant {
                    bot_uuid: "sender".to_string(),
                    bot_name: Some("Sender Bot".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
            ],
            messages: Vec::new(),
            workspace: Workspace::default(),
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::default(),
            dm_pair_key: None,
            group_strategy: GroupStrategy::Chat,
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
        }
    }

    /// Convenience: build an overlay row for a Bot. `uuid == "driver"` marks
    /// the driver — keep this in sync with `create_overlay_test_session`'s
    /// `driver_bot` field so overlay matches participant rows.
    fn ov_bot(
        uuid: &str,
        mode: Option<ParticipantMode>,
        status: ActorStatus,
    ) -> RouteParticipantOverlay {
        RouteParticipantOverlay {
            bot_uuid: uuid.to_string(),
            bot_name: None,
            actor_kind: ActorKind::Bot,
            mode,
            status,
            is_driver: uuid == "driver",
        }
    }

    /// Convenience: build an overlay row for a Human (Humans are never driver).
    fn ov_human(uuid: &str, mode: ParticipantMode, status: ActorStatus) -> RouteParticipantOverlay {
        RouteParticipantOverlay {
            bot_uuid: uuid.to_string(),
            bot_name: None,
            actor_kind: ActorKind::Human,
            mode: Some(mode),
            status,
            is_driver: false,
        }
    }

    fn target_for<'a>(decision: &'a RoutingDecision, uuid: &str) -> Option<&'a RoutingTarget> {
        decision.targets.iter().find(|t| t.bot_uuid == uuid)
    }

    fn create_human_bot_dm_session() -> Group {
        let mut session = create_overlay_test_session();
        session.group_kind = GroupKind::Dm;
        session.driver_bot = "bot_x".to_string();
        session.dm_pair_key = Some("bot_x|human_alice".to_string());
        session.participants = vec![
            Participant {
                bot_uuid: "human_alice".to_string(),
                bot_name: Some("Alice".to_string()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
            },
            Participant {
                bot_uuid: "bot_x".to_string(),
                bot_name: Some("Bot X".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
        ];
        session
    }

    fn create_bot_bot_dm_session() -> Group {
        let mut session = create_overlay_test_session();
        session.group_kind = GroupKind::Dm;
        session.driver_bot = "bot_a".to_string();
        session.dm_pair_key = Some("bot_a|bot_b".to_string());
        session.participants = vec![
            Participant {
                bot_uuid: "bot_a".to_string(),
                bot_name: Some("Bot A".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
            Participant {
                bot_uuid: "bot_b".to_string(),
                bot_name: Some("Bot B".to_string()),
                kind: None,
                role: ParticipantRole::Consultant,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
            },
        ];
        session
    }

    #[tokio::test]
    async fn test_dm_human_to_bot_routes_to_other_actor_with_send() {
        let router = MessageRouter::new();
        let session = create_human_bot_dm_session();
        let overlay = vec![
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
        ];

        let decision = router
            .route_dm_with_overlay(&session, "@human_alice ignored", "human_alice", &overlay)
            .await;

        assert_eq!(decision.targets.len(), 1);
        assert_eq!(decision.targets[0].bot_uuid, "bot_x");
        assert_eq!(decision.targets[0].delivery_type, DeliveryType::Send);
        assert_eq!(decision.mentions, vec!["human_alice".to_string()]);
        assert_eq!(decision.cleaned_message, "human_alice ignored");
    }

    #[tokio::test]
    async fn test_dm_human_to_muted_or_hidden_bot_is_inject() {
        let router = MessageRouter::new();
        let session = create_human_bot_dm_session();

        for overlay in [
            vec![
                ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
                ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
            ],
            vec![
                ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
                ov_bot("bot_x", None, ActorStatus::Hidden),
            ],
        ] {
            let decision = router
                .route_dm_with_overlay(&session, "hello", "human_alice", &overlay)
                .await;
            assert_eq!(decision.targets.len(), 1);
            assert_eq!(decision.targets[0].delivery_type, DeliveryType::Inject);
        }
    }

    #[tokio::test]
    async fn test_dm_bot_to_human_has_no_bot_targets() {
        let router = MessageRouter::new();
        let session = create_human_bot_dm_session();
        let overlay = vec![
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
        ];

        let decision = router
            .route_dm_with_overlay(&session, "done", "bot_x", &overlay)
            .await;

        assert!(decision.targets.is_empty());
        assert!(decision.mentions.is_empty());
    }

    #[tokio::test]
    async fn test_dm_bot_bot_routes_to_other_bot_and_excludes_sender() {
        let router = MessageRouter::new();
        let session = create_bot_bot_dm_session();
        let overlay = vec![
            ov_bot("bot_a", None, ActorStatus::Online),
            ov_bot("bot_b", None, ActorStatus::Online),
        ];

        let decision = router
            .route_dm_with_overlay(&session, "@bot_a should not self route", "bot_a", &overlay)
            .await;

        assert_eq!(decision.targets.len(), 1);
        assert_eq!(decision.targets[0].bot_uuid, "bot_b");
        assert_eq!(decision.targets[0].delivery_type, DeliveryType::Send);
        assert!(target_for(&decision, "bot_a").is_none());
    }

    // ---- 8-cell overlay matrix (mode × status × mention for Bot) ----

    /// Cell 1: Bot auto + online + NOT mentioned + non-driver → Inject.
    #[tokio::test]
    async fn test_overlay_bot_auto_online_unmentioned_nondriver_inject() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "ping no mention", Some("sender"), &overlay)
            .await;

        // sender excluded; driver gets Send (default no-mention rule); bot_x Inject
        assert!(target_for(&d, "sender").is_none());
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
        assert!(d.mentions.is_empty());
    }

    /// Cell 2: Bot auto + online + @-mentioned → Send.
    #[tokio::test]
    async fn test_overlay_bot_auto_online_mentioned_send() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Inject
        );
        assert_eq!(d.mentions, vec!["bot_x".to_string()]);
    }

    /// Cell 3: Bot muted + online + @-mentioned → forced Inject.
    #[tokio::test]
    async fn test_overlay_bot_muted_online_mentioned_forced_inject() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
            .await;

        // bot_x is mentioned but muted → Inject; driver is also Inject (mention branch)
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Cell 4: Bot auto + hidden + @-mentioned → forced Inject.
    #[tokio::test]
    async fn test_overlay_bot_auto_hidden_mentioned_forced_inject() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Hidden),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Cell 5: Bot muted + hidden + @-mentioned → still Inject (both downgrade).
    #[tokio::test]
    async fn test_overlay_bot_muted_hidden_mentioned_inject() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Hidden),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@bot_x please", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Cell 6: @ALL with one Bot muted → muted Bot still gets Inject; others Send.
    #[tokio::test]
    async fn test_overlay_at_all_with_muted_bot_downgraded() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", Some(ParticipantMode::Muted), ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@all heads up", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Cell 7: driver hidden + no mention → driver gets Inject (forced),
    /// other Bots remain Inject too (no mention, non-driver default).
    #[tokio::test]
    async fn test_overlay_driver_hidden_no_mention_driver_forced_inject() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Hidden),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "ping", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Inject
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Cell 8: All Bots online + no mention → only driver Send (sanity baseline).
    #[tokio::test]
    async fn test_overlay_all_online_no_mention_baseline() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "hello", Some("sender"), &overlay)
            .await;

        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    // ---- @Human path (X.3) ----

    /// @Human present: mention survives in `decision.mentions`, but no Bot
    /// is in `bot_mentions` so the no-mention branch fires → driver Send.
    /// Human never appears in `targets` (Bot-only).
    #[tokio::test]
    async fn test_overlay_at_present_human_driver_still_sends() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@human_alice 你怎么看", Some("sender"), &overlay)
            .await;

        // Human kept in mentions but absent from targets
        assert!(d.mentions.contains(&"human_alice".to_string()));
        assert!(target_for(&d, "human_alice").is_none());
        // No Bot mentioned → driver Send fallback
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// @Human absent: mention is dropped entirely, no Bot is mentioned →
    /// no-mention branch fires (driver Send). `mentions` MUST NOT contain
    /// the absent human.
    #[tokio::test]
    async fn test_overlay_at_absent_human_drops_mention() {
        let router = MessageRouter::new();
        // Mark human as absent in the session participants too (overlay alone
        // is the source of truth, but route_with_overlay falls back to
        // session.participants when overlay row is missing — keep both
        // consistent for clarity).
        let mut session = create_overlay_test_session();
        for p in session.participants.iter_mut() {
            if p.bot_uuid == "human_alice" {
                p.mode = Some(ParticipantMode::Absent);
            }
        }
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Absent, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(&session, "@human_alice 在吗", Some("sender"), &overlay)
            .await;

        // Absent human dropped from mentions
        assert!(!d.mentions.contains(&"human_alice".to_string()));
        // No Bot mentioned → driver Send
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Inject
        );
    }

    /// Mixed: @Bot + @Human present together → Bot mention takes effect (Bot
    /// branch fires), Human mention preserved in `mentions` for transcript.
    #[tokio::test]
    async fn test_overlay_at_bot_plus_present_human_bot_branch_wins() {
        let router = MessageRouter::new();
        let session = create_overlay_test_session();
        let overlay = vec![
            ov_bot("driver", None, ActorStatus::Online),
            ov_bot("bot_x", None, ActorStatus::Online),
            ov_human("human_alice", ParticipantMode::Present, ActorStatus::Online),
            ov_bot("sender", None, ActorStatus::Online),
        ];

        let d = router
            .route_with_overlay(
                &session,
                "@bot_x @human_alice please coordinate",
                Some("sender"),
                &overlay,
            )
            .await;

        // bot_x → Send (mentioned Bot), driver → Inject (mention branch)
        assert_eq!(
            target_for(&d, "bot_x").unwrap().delivery_type,
            DeliveryType::Send
        );
        assert_eq!(
            target_for(&d, "driver").unwrap().delivery_type,
            DeliveryType::Inject
        );
        // Both mentions surface for transcript
        assert!(d.mentions.contains(&"bot_x".to_string()));
        assert!(d.mentions.contains(&"human_alice".to_string()));
    }

    // -----------------------------------------------------------
    // ManagerWorker broadcast exclusion (Fix 6)
    // -----------------------------------------------------------

    fn create_manager_worker_session() -> Group {
        Group {
            id: "mw-session".to_string(),
            driver_bot: "manager".to_string(),
            group_strategy: GroupStrategy::ManagerWorker,
            participants: vec![
                Participant {
                    bot_uuid: "manager".to_string(),
                    bot_name: Some("Manager".to_string()),
                    kind: None,
                    role: ParticipantRole::Manager,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
                Participant {
                    bot_uuid: "worker1".to_string(),
                    bot_name: Some("Worker1".to_string()),
                    kind: None,
                    role: ParticipantRole::Worker,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
                Participant {
                    bot_uuid: "worker2".to_string(),
                    bot_name: Some("Worker2".to_string()),
                    kind: None,
                    role: ParticipantRole::Worker,
                    actor_kind: ActorKind::Bot,
                    mode: None,
                },
            ],
            ..create_test_session()
        }
    }

    #[tokio::test]
    async fn manager_worker_route_excludes_workers_on_no_mention() {
        let router = MessageRouter::new();
        let session = create_manager_worker_session();

        let decision = router
            .route(&session, "Hello, workers do your jobs", None)
            .await;

        // Manager (lead) is present, workers are excluded
        assert!(target_for(&decision, "manager").is_some());
        assert!(target_for(&decision, "worker1").is_none());
        assert!(target_for(&decision, "worker2").is_none());
    }

    #[tokio::test]
    async fn manager_worker_route_excludes_workers_even_when_mentioned() {
        let router = MessageRouter::new();
        let session = create_manager_worker_session();

        // @mention worker1 explicitly — it should still be excluded
        let decision = router
            .route(&session, "@worker1 please help", None)
            .await;

        // Manager is still present, but worker1 is excluded despite mention
        assert!(target_for(&decision, "manager").is_some());
        assert!(target_for(&decision, "worker1").is_none());
    }

    #[tokio::test]
    async fn manager_worker_route_with_overlay_excludes_workers() {
        let router = MessageRouter::new();
        let session = create_manager_worker_session();
        let overlay = vec![
            ov_bot("manager", None, ActorStatus::Online),
            ov_bot("worker1", None, ActorStatus::Online),
            ov_bot("worker2", None, ActorStatus::Online),
        ];

        let decision = router
            .route_with_overlay(
                &session,
                "@worker1 do this task",
                Some("sender"),
                &overlay,
            )
            .await;

        // Manager is present, workers excluded from broadcast table
        assert!(target_for(&decision, "manager").is_some());
        assert!(target_for(&decision, "worker1").is_none());
        assert!(target_for(&decision, "worker2").is_none());
    }
}
