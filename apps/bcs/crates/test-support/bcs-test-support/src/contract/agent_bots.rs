use bcs_service_api::{BotRepoPort, types::agent_registration::AgentBotQuery};

/// Additional identity-query coverage shared by storage adapter conformance tests.
pub async fn agent_bot_query_contract_tests(repo: &dyn BotRepoPort, query: &AgentBotQuery, expected: &[&str]) {
    let bots = repo.query_agent_bots(query).await.expect("authoritative Agent Bot query");
    assert!(bots.len() <= query.limit);
    assert_eq!(bots.iter().map(|bot| bot.bot.bot_id.as_str()).collect::<Vec<_>>(), expected);
    for bot in &bots {
        assert_eq!(bot.agent_code, query.agent_code);
        if let Some(actor) = &query.bot_uuid { assert_eq!(&bot.bot.bot_id, actor); }
    }
}
