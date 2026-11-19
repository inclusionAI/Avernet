use super::*;

impl BcsChannelService {
    pub(super) async fn resolve_exclusive_session_speaker(
        &self,
        session: &Session,
        group: &Group,
    ) -> Result<Option<String>, ChannelUseCaseError> {
        if session.group_id != group.id || group.group_kind != GroupKind::Normal
            || group.group_strategy != GroupStrategy::Chat || session.session_kind != SessionKind::Chat
        {
            return Ok(None);
        }
        let Some(route) = self.channel_route_from_session_meta(session) else {
            return Ok(None);
        };
        let Some(user_id) = route.im_user_id.as_deref().filter(|id| !id.trim().is_empty()) else {
            return Ok(None);
        };
        if route.session_scope != SessionScope::PerSender || route.im_conversation_type != "2" {
            return Ok(None);
        }
        // Session metadata may be supplied by clients and is only a lookup
        // hint. Only a live, matching Channel-owned row can grant exclusivity.
        let Some(binding) = self.bindings.get(&route.binding_id).await? else {
            return Ok(None);
        };
        let BindingTarget::Bot { bot_id } = &binding.target else {
            return Ok(None);
        };
        if binding.status != BindingStatus::Active || binding.env != self.env
            || session.env.as_deref().is_some_and(|env| env != self.env)
            || binding.group_chat_scope == Some(GroupChatScope::ConversationShared)
            || group.driver_bot != *bot_id
        {
            return Ok(None);
        }
        let canonical_group = channel_owned_group_id(&binding.channel_type, GroupKind::Normal, &binding.id)?;
        if group.id != canonical_group
            && group.id != legacy_channel_owned_group_id(&binding.channel_type, &binding.id)
        {
            return Ok(None);
        }
        let principal = format!("{}:{}:{}", binding.channel_type, route.im_conversation_id, user_id);
        if session.caller_principal.as_deref() != Some(principal.as_str()) {
            return Ok(None);
        }
        let Some(mapped) = self.conversations.get(
            &binding.id, &route.im_conversation_id, SessionScope::PerSender, Some(user_id),
        ).await? else {
            return Ok(None);
        };
        if mapped.bcs_session_id != session.id || mapped.im_conversation_type != "2"
            || mapped.session_scope != SessionScope::PerSender
            || mapped.im_user_id.as_deref() != Some(user_id)
        {
            return Ok(None);
        }
        let speaker = human_actor_id(user_id);
        let mut humans = group.participants.iter().filter(|member| member.is_human());
        let mut bots = group.participants.iter().filter(|member| member.is_bot());
        if !humans.next().is_some_and(|human| {
            human.bot_uuid == speaker && human.effective_mode() != ParticipantMode::Absent
        }) || humans.next().is_some()
            || !bots.next().is_some_and(|bot| bot.bot_uuid == *bot_id)
            || bots.next().is_some()
        {
            return Ok(None);
        }
        Ok(Some(speaker))
    }
}
