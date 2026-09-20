use super::*;

pub(super) fn resolve_group_chat_sender(cmd: &GroupChatCommand) -> ServiceResult<String> {
    if let Some(sender) = cmd
        .requested_sender_id
        .as_deref()
        .filter(|sender| !sender.is_empty())
    {
        return Ok(sender.to_string());
    }

    caller_actor_id(&cmd.caller).ok_or_else(|| {
        ServiceError::Unauthorized(
            "valid Human cookie or Bot token is required for this group message request"
                .to_string(),
        )
    })
}

fn caller_actor_id(caller: &CallerContext) -> Option<String> {
    match caller {
        CallerContext::Human(human) => Some(human.actor_id.clone()),
        CallerContext::Bot(bot) => Some(bot.bot_uuid.clone()),
        _ => None,
    }
}

pub(super) async fn verify_group_chat_caller_access(
    flow: &BcsMessageFlow,
    group: &Group,
    caller: &CallerContext,
) -> ServiceResult<()> {
    match caller {
        CallerContext::Bot(bot) => match group.get_participant(&bot.bot_uuid) {
            Some(participant) if participant.is_bot() => Ok(()),
            _ => Err(ServiceError::Unauthorized(format!(
                "bot '{}' is not a participant of group '{}'",
                bot.bot_uuid, group.id
            ))),
        },
        CallerContext::Human(human) => {
            if human_has_group_access(flow, group, &human.actor_id, &human.staff_no).await {
                Ok(())
            } else {
                Err(ServiceError::Unauthorized(format!(
                    "current Human '{}' is not a participant and owns no Bot in group '{}'",
                    human.actor_id, group.id
                )))
            }
        }
        _ => Err(ServiceError::Unauthorized(
            "valid Human cookie or Bot token is required for this group message request"
                .to_string(),
        )),
    }
}

pub(super) async fn verify_group_chat_sender(
    flow: &BcsMessageFlow,
    group: &Group,
    sender: &str,
    caller: &CallerContext,
) -> ServiceResult<()> {
    if sender.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "sender is required".to_string(),
            request_id: None,
        });
    }

    match caller {
        CallerContext::Bot(bot) => {
            if sender != bot.bot_uuid {
                return Err(ServiceError::Unauthorized(format!(
                    "bot caller '{}' cannot speak as another sender '{}'",
                    bot.bot_uuid, sender
                )));
            }
        }
        CallerContext::Human(_) => {
            verify_http_group_message_sender(flow, group, sender, caller).await?;
        }
        _ => {
            return Err(ServiceError::Unauthorized(
                "valid Human cookie or Bot token is required for this group message request"
                    .to_string(),
            ));
        }
    }

    if group.get_participant(sender).is_none() {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' is not a participant of group '{}'",
            sender, group.id
        )));
    }
    Ok(())
}

pub(super) async fn verify_http_group_message_sender(
    flow: &BcsMessageFlow,
    group: &Group,
    sender: &str,
    caller: &CallerContext,
) -> ServiceResult<()> {
    if sender.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "sender is required".to_string(),
            request_id: None,
        });
    }

    let CallerContext::Human(human) = caller else {
        return Err(ServiceError::Unauthorized(
            "valid Human cookie is required for this group message request".to_string(),
        ));
    };

    if sender == human.actor_id {
        return Ok(());
    }

    if is_human_bot_dm(group) {
        return Err(ServiceError::Unauthorized(format!(
            "sender '{}' must be the current Human '{}' in Human-Bot DM group '{}'",
            sender, human.actor_id, group.id
        )));
    }

    if let Some(bot) = flow.registry.get(sender).await {
        if bot.actor_kind == ActorKind::Bot
            && bot_belongs_to_staff(sender, bot.created_by.as_deref(), &human.staff_no)
        {
            return Ok(());
        }
    }

    Err(ServiceError::Unauthorized(format!(
        "sender '{}' must be the current Human '{}' or a Bot owned by them",
        sender, human.actor_id
    )))
}

pub(super) fn is_human_bot_dm(group: &Group) -> bool {
    group.group_kind == GroupKind::Dm
        && group
            .participants
            .iter()
            .any(|participant| participant.actor_kind == ActorKind::Human)
        && group
            .participants
            .iter()
            .any(|participant| participant.actor_kind == ActorKind::Bot)
}

pub(super) async fn verify_http_group_message_caller_access(
    flow: &BcsMessageFlow,
    group: &Group,
    caller: &CallerContext,
) -> ServiceResult<()> {
    let CallerContext::Human(human) = caller else {
        return Err(ServiceError::Unauthorized(
            "valid Human cookie is required for this group message request".to_string(),
        ));
    };

    if human_has_group_access(flow, group, &human.actor_id, &human.staff_no).await {
        Ok(())
    } else {
        Err(ServiceError::Unauthorized(format!(
            "current Human '{}' is not a participant and owns no Bot in group '{}'",
            human.actor_id, group.id
        )))
    }
}

async fn human_has_group_access(
    flow: &BcsMessageFlow,
    group: &Group,
    actor_id: &str,
    staff_no: &str,
) -> bool {
    if group
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == actor_id)
    {
        return true;
    }

    for participant in group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
    {
        let Some(bot) = flow.registry.get(&participant.bot_uuid).await else {
            continue;
        };
        if bot_belongs_to_staff(&participant.bot_uuid, bot.created_by.as_deref(), staff_no) {
            return true;
        }
    }

    false
}

fn bot_belongs_to_staff(bot_uuid: &str, created_by: Option<&str>, staff_no: &str) -> bool {
    if created_by == Some(staff_no) {
        return true;
    }
    if created_by.is_some() {
        return false;
    }
    bot_uuid
        .rsplit_once(':')
        .map(|(_, suffix)| suffix == staff_no)
        .unwrap_or(false)
}

pub(super) async fn sender_name_for_chat(
    flow: &BcsMessageFlow,
    caller: &CallerContext,
    sender_id: &str,
) -> Option<String> {
    // A Human caller speaks either as themselves or as one of their owned bots
    // (the latter is authorized by `verify_group_chat_sender`). In both cases
    // the on-wire actor identity is the Human's staff_no, not a bot display
    // name, so resolve the staff_no before consulting the bot registry.
    if let CallerContext::Human(human) = caller {
        return Some(human.staff_no.clone());
    }

    if let Some(bot) = flow.registry.get(sender_id).await {
        if let Some(name) = bot
            .capabilities
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
        {
            return Some(name.to_string());
        }
    }

    None
}

pub(super) async fn build_route_overlay(flow: &BcsMessageFlow, group: &Group) -> Vec<RouteParticipantOverlay> {
    let mut overlay = Vec::with_capacity(group.participants.len());
    for participant in &group.participants {
        let status = flow
            .registry
            .get(&participant.bot_uuid)
            .await
            .map(|bot| bot.status)
            .unwrap_or(ActorStatus::Online);
        overlay.push(RouteParticipantOverlay {
            bot_uuid: participant.bot_uuid.clone(),
            bot_name: participant.bot_name.clone(),
            actor_kind: participant.actor_kind,
            mode: participant.mode,
            status,
            is_driver: participant.bot_uuid == group.driver_bot,
        });
    }
    overlay
}

pub(super) fn build_explicit_mention_decision(
    group: &Group,
    mention_uuids: &[String],
    message: &str,
    overlay: &[RouteParticipantOverlay],
) -> RoutingDecision {
    let overlay_map: HashMap<&str, &RouteParticipantOverlay> =
        overlay.iter().map(|o| (o.bot_uuid.as_str(), o)).collect();

    let mut valid_mentions: Vec<String> = Vec::new();
    let mut hidden_mentions: Vec<HiddenMentionInfo> = Vec::new();
    for mention in mention_uuids {
        if !group.participants.iter().any(|p| p.bot_uuid == *mention) {
            continue;
        }
        let is_hidden = overlay_map
            .get(mention.as_str())
            .map_or(false, |o| o.status == ActorStatus::Hidden);
        if is_hidden {
            let bot_name = overlay_map
                .get(mention.as_str())
                .and_then(|o| o.bot_name.clone())
                .unwrap_or_else(|| mention.clone());
            hidden_mentions.push(HiddenMentionInfo {
                hidden_bot_id: mention.clone(),
                hidden_bot_name: bot_name,
            });
        } else {
            valid_mentions.push(mention.clone());
        }
    }

    let targets = group
        .participants
        .iter()
        .filter(|participant| participant.is_bot())
        .filter(|participant| {
            if group.group_strategy == GroupStrategy::ManagerWorker
                && participant.role != group.group_strategy.lead_role()
            {
                return false;
            }
            true
        })
        .map(|participant| {
            let is_mentioned = valid_mentions.contains(&participant.bot_uuid);
            let delivery_type = if !valid_mentions.is_empty() {
                if is_mentioned {
                    DeliveryType::Send
                } else {
                    DeliveryType::Inject
                }
            } else if participant.role == group.group_strategy.lead_role() {
                DeliveryType::Send
            } else {
                DeliveryType::Inject
            };

            RoutingTarget {
                bot_uuid: participant.bot_uuid.clone(),
                url: String::new(),
                is_driver: participant.role == group.group_strategy.lead_role(),
                delivery_type,
            }
        })
        .collect();

    let cleaned_message = Regex::new(r"@([\w\p{Unified_Ideograph}:]+)")
        .map(|regex| regex.replace_all(message, "$1").to_string())
        .unwrap_or_else(|_| message.to_string());

    apply_overlay_to_decision(
        RoutingDecision {
            targets,
            mentions: valid_mentions,
            cleaned_message,
            hidden_mentions,
        },
        overlay,
    )
}

pub(super) fn callback_routable_message(group: &Group, cmd: &GroupCallbackCommand) -> String {
    if cmd.mentions.is_empty() {
        return cmd.message.clone();
    }

    if mentions_all(&cmd.mentions) {
        return format!("@all {}", cmd.message);
    }

    let mention_prefixes: Vec<String> = cmd
        .mentions
        .iter()
        .map(|mention| {
            if let Some(participant) = group.participants.iter().find(|p| p.bot_uuid == *mention) {
                if let Some(ref name) = participant.bot_name {
                    return format!("@{}", name);
                }
            }
            format!("@{}", mention)
        })
        .collect();
    format!("{} {}", mention_prefixes.join(" "), cmd.message)
}

pub(super) fn mentions_all(mentions: &[String]) -> bool {
    mentions
        .iter()
        .any(|mention| mention.eq_ignore_ascii_case("@all") || mention.eq_ignore_ascii_case("all"))
}

pub(super) fn delivery_result_summary(
    bot_uuid: &str,
    delivery_type: DeliveryType,
    success: bool,
    error: Option<String>,
) -> MessageDeliveryResult {
    MessageDeliveryResult {
        bot_uuid: bot_uuid.to_string(),
        delivery_type,
        success,
        error,
    }
}

pub(crate) fn apply_overlay_to_decision(
    mut decision: RoutingDecision,
    overlay: &[RouteParticipantOverlay],
) -> RoutingDecision {
    use std::collections::{HashMap, HashSet};

    let overlay_map: HashMap<&str, &RouteParticipantOverlay> = overlay
        .iter()
        .map(|row| (row.bot_uuid.as_str(), row))
        .collect();

    let absent_humans: HashSet<String> = overlay
        .iter()
        .filter(|row| {
            row.actor_kind == ActorKind::Human
                && row
                    .mode
                    .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind))
                    == ParticipantMode::Absent
        })
        .map(|row| row.bot_uuid.clone())
        .collect();

    if !absent_humans.is_empty() {
        decision
            .mentions
            .retain(|mention| !absent_humans.contains(mention));
    }

    decision.targets = decision
        .targets
        .into_iter()
        .filter_map(|mut target| {
            if absent_humans.contains(&target.bot_uuid) {
                return None;
            }
            if let Some(row) = overlay_map.get(target.bot_uuid.as_str()) {
                let effective_mode = row
                    .mode
                    .unwrap_or_else(|| ParticipantMode::default_for(row.actor_kind));
                let forced_inject =
                    effective_mode == ParticipantMode::Muted || row.status == ActorStatus::Hidden;
                if target.delivery_type == DeliveryType::Send && forced_inject {
                    target.delivery_type = DeliveryType::Inject;
                    if row.status == ActorStatus::Hidden {
                        let bot_name = row
                            .bot_name
                            .clone()
                            .unwrap_or_else(|| target.bot_uuid.clone());
                        decision.hidden_mentions.push(HiddenMentionInfo {
                            hidden_bot_id: target.bot_uuid.clone(),
                            hidden_bot_name: bot_name,
                        });
                    }
                }
            }
            Some(target)
        })
        .collect();

    decision.mentions.retain(|m| {
        !decision
            .hidden_mentions
            .iter()
            .any(|h| h.hidden_bot_id == *m)
    });

    decision
}

pub(super) async fn sender_display_name(flow: &BcsMessageFlow, actor_id: &str) -> String {
    match flow.registry.get(actor_id).await {
        Some(bot) => bot.capabilities.name.clone().unwrap_or_else(|| {
            warn!(
                actor_id = %actor_id,
                phase = "from_name",
                "bcs_bots.name is empty for sender; returning blank from_name"
            );
            String::new()
        }),
        None => {
            warn!(
                actor_id = %actor_id,
                phase = "from_name",
                "registry has no row for sender; returning blank from_name"
            );
            String::new()
        }
    }
}

pub(super) async fn preferred_sender_display_name(flow: &BcsMessageFlow, cmd: &WebSendCommand) -> String {
    if let Some(name) = cmd
        .from_name
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
    {
        return name.to_string();
    }
    sender_display_name(flow, &cmd.from_actor_id).await
}

pub(super) async fn from_bot_owner(flow: &BcsMessageFlow, actor_id: &str) -> Option<String> {
    if actor_id.starts_with("human_") {
        None
    } else {
        flow.registry
            .get(actor_id)
            .await
            .and_then(|bot| bot.created_by)
    }
}
