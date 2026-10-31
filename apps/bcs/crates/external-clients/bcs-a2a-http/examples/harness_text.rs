//! Listening HTTP smoke for the official Rust client against the Java adapter.
//! Credentials are environment-only; this is not the full BCS delivery E2E.
use std::{error::Error, time::Duration};
use futures::StreamExt;
use bcs_a2a_http::{card::select_card, client::bind_rest_client,
    projection::{Output, Projection}};
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION};
use serde_json::json;

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let base = std::env::var("AVERNET_A2A_URL")?;
    let token = std::env::var("AVERNET_A2A_BEARER")?;
    let url = reqwest::Url::parse(&base)?;
    if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") {
        return Err("This smoke example only permits explicit loopback HTTP".into());
    }
    let mut headers = HeaderMap::new();
    let mut bearer = HeaderValue::from_str(&format!("Bearer {token}"))?;
    bearer.set_sensitive(true);
    headers.insert(AUTHORIZATION, bearer);
    let http = reqwest::Client::builder().default_headers(headers)
        .redirect(reqwest::redirect::Policy::none()).no_proxy()
        .connect_timeout(Duration::from_secs(10)).timeout(Duration::from_secs(180)).build()?;
    let raw = http.get(format!("{}/agent-card.json", base.trim_end_matches('/')))
        .send().await?.error_for_status()?.json().await?;
    let selected = select_card(raw, &[], true, |selected| selected == base)
        .map_err(|error| format!("Card incompatible: {error:?}"))?;
    let client = bind_rest_client(http, selected.interface.url, selected.interface.tenant);
    let message_id = format!("rust-smoke-{}", std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?.as_nanos());
    let request = serde_json::from_value(json!({"message":{
        "messageId":message_id,"role":"ROLE_USER",
        "parts":[{"text":"只回复：Rust和Java的A2A已连通。不要调用工具。"}]}}))?;
    let mut stream = client.send_streaming_message(&request).await?;
    let mut projection = Projection::default();
    let mut answer = String::new();
    let mut complete = false;
    let mut count = 0;
    while let Some(event) = stream.next().await {
        let (next, outputs) = projection.stage(event?).map_err(|e| format!("Projection failed: {e:?}"))?;
        for output in outputs {
            match output {
                Output::Delta(text) => answer.push_str(&text),
                Output::Status(a2a::TaskState::Completed) => complete = true,
                Output::Status(a2a::TaskState::Failed | a2a::TaskState::Canceled | a2a::TaskState::Rejected) =>
                    return Err("Remote Task ended unsuccessfully".into()),
                _ => {}
            }
        }
        projection = next;
        count += 1;
    }
    if !complete || answer.is_empty() { return Err("Stream ended without a completed answer".into()); }
    println!("{}", json!({"status":"passed","scope":"Rust SDK -> Java Harness -> real configured model",
        "events":count,"answer":answer}));
    Ok(())
}
