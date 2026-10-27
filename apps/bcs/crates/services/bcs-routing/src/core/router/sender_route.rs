use super::*;

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
