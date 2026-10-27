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
    Group, GroupKind, GroupStrategy, HiddenMentionInfo, Participant,
    ParticipantMode, ParticipantRole, ResponseMode, RouteAndSendResult, RouteParticipantOverlay,
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

fn is_unified_ideograph(ch: char) -> bool {
    matches!(
        ch,
        '\u{3400}'..='\u{4DBF}'
            | '\u{4E00}'..='\u{9FFF}'
            | '\u{F900}'..='\u{FAFF}'
            | '\u{20000}'..='\u{2FA1F}'
    )
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
        || is_unified_ideograph(next)
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

mod sender_route;
pub use sender_route::build_sender_route_decision;

#[cfg(test)]
mod tests;
