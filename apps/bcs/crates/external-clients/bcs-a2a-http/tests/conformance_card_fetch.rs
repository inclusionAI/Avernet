use bcs_a2a_http::card_fetch::HttpA2aCardFetcher;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::port::a2a_card::A2aCardPort;
use serde_json::json;
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::TcpListener};

async fn server(status: &str, headers: &str, body: String) -> (String, tokio::task::JoinHandle<String>) {
    let socket = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/card", socket.local_addr().unwrap());
    let response = format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n{body}", body.len());
    let handle = tokio::spawn(async move {
        let (mut stream, _) = socket.accept().await.unwrap();
        let mut request = vec![0; 8192];
        let size = stream.read(&mut request).await.unwrap();
        stream.write_all(response.as_bytes()).await.unwrap();
        String::from_utf8(request[..size].to_vec()).unwrap()
    });
    (url, handle)
}

fn card() -> String {
    json!({"name":"demo", "description":"test", "version":"1",
        "supportedInterfaces":[{"url":"http://127.0.0.1:9000/a2a/bots/demo",
            "protocolBinding":"HTTP+JSON", "protocolVersion":"1.0"}],
        "capabilities":{"streaming":true}, "defaultInputModes":["text/plain"],
        "defaultOutputModes":["text/plain"], "skills":[], "future":{"keep":true}}).to_string()
}

#[tokio::test]
async fn public_card_is_fetched_without_credentials_and_preserved() {
    let (url, request) = server("200 OK", "Content-Type: application/json; charset=UTF-8\r\nCache-Control: max-age=300\r\n", card()).await;
    let fetcher = HttpA2aCardFetcher::new(OutboundUrlGuard::new(true, true), vec![]);
    let validated = fetcher.fetch(&url, true).await.unwrap();
    assert_eq!(validated.raw["future"]["keep"], true);
    let request = request.await.unwrap().to_ascii_lowercase();
    assert!(request.starts_with("get /card "));
    assert!(!request.contains("authorization:"));
}

#[tokio::test]
async fn rejects_redirects_media_type_cache_and_oversized_body() {
    for (status, headers, body) in [
        ("302 Found", "Location: http://127.0.0.1:1/secret\r\n", String::new()),
        ("200 OK", "Content-Type: text/html\r\n", card()),
        ("200 OK", "Content-Type: application/json\r\nCache-Control: max-age=300\r\nCache-Control: No-Cache=\"field\"\r\n", card()),
        ("200 OK", "Content-Type: application/json\r\nCache-Control: no-store\r\n", card()),
        ("200 OK", "Content-Type: application/json\r\n", " ".repeat(1_048_577)),
    ] {
        let (url, request) = server(status, headers, body).await;
        let fetcher = HttpA2aCardFetcher::new(OutboundUrlGuard::new(true, true), vec![]);
        assert!(fetcher.fetch(&url, true).await.is_err());
        // Large rejected bodies can close before the test server finishes writing.
        let _ = request.await;
    }
}

#[tokio::test]
async fn outbound_policy_is_enforced_for_card_and_selected_interface() {
    let fetcher = HttpA2aCardFetcher::new(OutboundUrlGuard::strict(), vec![]);
    assert!(fetcher.fetch("http://127.0.0.1:1/card", true).await.is_err());
    let mut body: serde_json::Value = serde_json::from_str(&card()).unwrap();
    body["supportedInterfaces"][0]["url"] = json!("https://169.254.169.254/agent");
    let (url, request) = server("200 OK", "Content-Type: application/json\r\n", body.to_string()).await;
    let fetcher = HttpA2aCardFetcher::new(OutboundUrlGuard::new(true, true), vec![]);
    assert!(fetcher.fetch(&url, true).await.is_err());
    request.await.unwrap();
}

#[tokio::test]
async fn http_card_fetch_conformance() {
    use bcs_test_support::contract::a2a_bot::a2a_card_port_contract_tests;
    let (url, request) = server("200 OK", "Content-Type: application/json\r\n", card()).await;
    let fetcher = HttpA2aCardFetcher::new(OutboundUrlGuard::new(true, true), vec![]);
    a2a_card_port_contract_tests(&fetcher, &url).await;
    assert!(request.await.unwrap().starts_with("GET /card "));
}

#[tokio::test]
async fn inline_card_uses_the_same_validation_without_fetching_or_executing() {
    let listener=TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut raw:serde_json::Value=serde_json::from_str(&card()).unwrap();
    raw["supportedInterfaces"][0]["url"]=json!(format!("http://{}/a2a",listener.local_addr().unwrap()));
    raw["unknown"] = json!({"retained":true});
    let fetcher=HttpA2aCardFetcher::new(OutboundUrlGuard::new(true,true),vec![]);
    let checked=fetcher.validate(raw.clone(),true).await.unwrap();assert_eq!(checked.raw,raw);
    let derived=bcs_service_api::types::a2a_bot::select_a2a_interface(&raw,|binding,url|binding==bcs_service_api::types::a2a_bot::A2aBinding::HttpJson && OutboundUrlGuard::new(true,true).validate_configured_http_url(url).is_ok()).unwrap();
    assert_eq!(derived,checked.interface);
    assert!(tokio::time::timeout(std::time::Duration::from_millis(25),listener.accept()).await.is_err());
    raw["capabilities"]["extensions"]=json!([{"uri":"https://example.org/unknown","required":true}]);
    assert!(fetcher.validate(raw,true).await.is_err());
    assert!(fetcher.validate(json!(["not a Card"]),true).await.is_err());
}
