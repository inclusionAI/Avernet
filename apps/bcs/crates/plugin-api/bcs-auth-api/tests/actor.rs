use async_trait::async_trait;
use axum::http::{HeaderMap, HeaderValue};
use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource};

struct Fixed { priority: u8, bot: Option<&'static str> }

#[async_trait]
impl AuthPlugin for Fixed {
    fn can_authenticate(&self, _: &HeaderMap) -> bool { true }
    async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        let mut p = AuthPrincipal::new(AuthSource::SessionToken);
        p.bot_uuid = self.bot.map(str::to_string);
        p.user_id = self.bot.is_none().then(|| "user".into());
        Ok(Some(p))
    }
    fn priority(&self) -> u8 { self.priority }
    fn name(&self) -> &'static str { "fixed" }
}

fn headers(actor: &str) -> HeaderMap {
    let mut h = HeaderMap::new();
    h.insert("x-bcn-actor-bot-id", HeaderValue::from_str(actor).unwrap());
    h
}

#[tokio::test]
async fn actor_requires_bot_and_skips_user_only_success() {
    let chain = AuthPluginChain::new(vec![
        Box::new(Fixed { priority: 10, bot: None }),
        Box::new(Fixed { priority: 30, bot: Some("bot_A") }),
    ]);
    assert_eq!(chain.authenticate(&headers("bot_A")).await.unwrap().principal.unwrap().bot_uuid.as_deref(), Some("bot_A"));
    assert!(chain.authenticate(&headers("bot_B")).await.is_err());
    assert!(chain.authenticate(&HeaderMap::new()).await.unwrap().principal.unwrap().user_id.is_some());
}

#[tokio::test]
async fn user_only_chain_cannot_satisfy_actor() {
    let chain = AuthPluginChain::new(vec![Box::new(Fixed { priority: 10, bot: None })]);
    assert!(chain.authenticate(&headers("bot_A")).await.is_err());
}

#[tokio::test]
async fn empty_or_repeated_actor_is_invalid() {
    let chain = AuthPluginChain::new(vec![Box::new(Fixed { priority: 10, bot: Some("bot_A") })]);
    assert!(chain.authenticate(&headers("")).await.is_err());
    let mut h = headers("bot_A");
    h.append("x-bcn-actor-bot-id", HeaderValue::from_static("bot_A"));
    assert!(chain.authenticate(&h).await.is_err());
}
