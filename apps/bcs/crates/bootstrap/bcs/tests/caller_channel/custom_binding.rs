//! Synthetic deployment plugin linked only into the test-utils server.
//! The peer is a real local HTTP server; all BCS work enters through HTTP/WS.
use std::{sync::Arc, time::Duration};
use a2a_e2e_sdk as a2a;
use bcs_a2a_binding_api::{Binding, Client, Events, Registry};
use bcs_a2a_http::client::{RestClient, bind_rest_client};
use serde_json::Value;

pub fn install(url: String) -> anyhow::Result<()> {
    let parsed = reqwest_e2e::Url::parse(&url)?;
    anyhow::ensure!(parsed.scheme()=="http" && parsed.host_str()==Some("127.0.0.1")
        && parsed.port().is_some() && parsed.path()=="/a2a" && parsed.username().is_empty()
        && parsed.password().is_none() && parsed.query().is_none() && parsed.fragment().is_none(), "local fixture endpoint required");
    let registry=Registry::new(vec![Arc::new(LocalBinding(url))]).map_err(anyhow::Error::msg)?;
    bcs_a2a_binding_api::install(registry).map_err(anyhow::Error::msg)
}
struct LocalBinding(String);
impl Binding for LocalBinding {
    fn name(&self)->&str { "LOCAL_EXAMPLE" }
    fn resolve(&self, declaration:&Value, _:bool)->Result<String,String> {
        if declaration.get("target").and_then(Value::as_str)!=Some("echo") || declaration.get("url").is_some() {
            return Err("UNKNOWN_TARGET".into());
        }
        Ok("echo".into())
    }
    fn fingerprint(&self,key:&str)->Result<String,String> {
        if key!="echo" {return Err("UNKNOWN_TARGET".into());}
        Ok(self.0.clone())
    }
    fn client(&self,key:&str,tenant:Option<&str>,bearer:Option<&str>,extensions:&str,timeout:Duration)->Result<Arc<dyn Client>,String> {
        self.fingerprint(key)?;
        let mut headers=reqwest_e2e::header::HeaderMap::new();
        if let Some(bearer)=bearer {
            let mut value=reqwest_e2e::header::HeaderValue::from_str(&format!("Bearer {bearer}")).map_err(|_|"INVALID_CREDENTIAL")?;
            value.set_sensitive(true);headers.insert("authorization",value);
        }
        if !extensions.is_empty() {headers.insert("a2a-extensions",extensions.parse().map_err(|_|"INVALID_EXTENSION")?);}
        let http=reqwest_e2e::Client::builder().no_proxy().redirect(reqwest_e2e::redirect::Policy::none())
            .default_headers(headers).timeout(timeout).build().map_err(|_|"CLIENT_UNAVAILABLE")?;
        Ok(Arc::new(LocalClient(bind_rest_client(http,self.0.clone(),tenant.map(str::to_owned)))))
    }
}
struct LocalClient(RestClient);
#[async_trait::async_trait]
impl Client for LocalClient {
    async fn send_message(&self,r:&a2a::SendMessageRequest)->Result<a2a::SendMessageResponse,a2a::A2AError>{self.0.send_message(r).await}
    async fn send_streaming_message(&self,r:&a2a::SendMessageRequest)->Result<Events,a2a::A2AError>{self.0.send_streaming_message(r).await}
    async fn get_task(&self,r:&a2a::GetTaskRequest)->Result<a2a::Task,a2a::A2AError>{self.0.get_task(r).await}
    async fn cancel_task(&self,r:&a2a::CancelTaskRequest)->Result<a2a::Task,a2a::A2AError>{self.0.cancel_task(r).await}
    async fn subscribe_to_task(&self,r:&a2a::SubscribeToTaskRequest)->Result<Events,a2a::A2AError>{self.0.subscribe_to_task(r).await}
}
