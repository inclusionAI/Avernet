use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_auth_api::{AuthError,AuthPlugin,AuthPluginChain,AuthPrincipal,AuthSource};

struct Parent;
#[async_trait]
impl AuthPlugin for Parent {
    fn can_authenticate(&self,_:&HeaderMap)->bool{true}
    async fn authenticate(&self,_:&HeaderMap)->Result<Option<AuthPrincipal>,AuthError>{
        let mut p=AuthPrincipal::new(AuthSource::AgentPass);p.bot_uuid=Some("a".into());p.token=Some("a.b.c".into());Ok(Some(p))
    }
    fn name(&self)->&'static str{"parent"}
    fn priority(&self)->u8{0}
}
#[tokio::test]
async fn conflicting_explicit_credentials_cannot_end_authentication() {
    let chain=AuthPluginChain::new(vec![Box::new(Parent)]);
    for bearer in ["static-a","a.b.c"] {
        let mut headers=HeaderMap::new();headers.insert("authorization",format!("Bearer {bearer}").parse().unwrap());
        headers.insert("x-bcs-bot-token","static-b".parse().unwrap());
        assert!(chain.authenticate(&headers).await.is_err());
    }
    let mut headers=HeaderMap::new();headers.append("authorization","Bearer a.b.c".parse().unwrap());headers.append("authorization","Bearer a.b.c".parse().unwrap());
    assert!(chain.authenticate(&headers).await.is_err());
}
