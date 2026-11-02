//! Card reads occur after existing Bot authorization/filtering, without caching
//! a second capability projection or deriving BCS policy from remote claims.
use super::*;
use serde_json::Value;

impl Bot {
    pub(super) async fn cards_for_ids(&self, ids: &[String]) -> Result<BTreeMap<String, Value>, BotUseCaseError> {
        match &self.a2a_queries {
            Some(core) => Ok(core.cards_for_bots(ids).await?),
            None => Ok(BTreeMap::new()), // Legacy/memory composition has no A2A registry.
        }
    }

    pub(super) async fn attach_query_cards(&self, entries: &mut [BotQueryEntry]) -> Result<(), BotUseCaseError> {
        let ids = entries.iter().map(|entry|entry.bot_uuid.clone()).collect::<Vec<_>>();
        let mut cards = self.cards_for_ids(&ids).await?;
        for entry in entries { entry.agent_card = cards.remove(&entry.bot_uuid); }
        Ok(())
    }

    pub(super) async fn filter_discovery_cards(&self, mut entries: Vec<BotDiscoveryEntry>, command: &BotDiscoveryCommand)
        -> Result<Vec<BotDiscoveryEntry>, BotUseCaseError> {
        let ids = entries.iter().map(|entry|entry.bot_uuid.clone()).collect::<Vec<_>>();
        let mut cards = match &self.a2a_queries {
            Some(core) => core.discovery_cards(&ids,command.q.as_deref(),&command.skills).await?,
            None => BTreeMap::new(),
        };
        entries.retain_mut(|entry| {
            match cards.remove(&entry.bot_uuid) {
                Some(Some(card)) => { entry.agent_card=Some(card);true }
                Some(None) => false,
                None => {
                let bot = RegisteredBot { bot_uuid:entry.bot_uuid.clone(),capabilities:entry.capabilities.clone(),
                    status:ActorStatus::Online,actor_kind:ActorKind::Bot,env:None,created_by:None };
                matches_discovery_selector(&bot, command)
                }
            }
        });
        Ok(entries)
    }
}
