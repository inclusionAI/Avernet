use bcs_storage_api::{StorageHealth, StoragePlugin};
use bcs_storage_baas::{config::BaasConfig, BaasStoragePlugin};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn plugin(server_url: String) -> BaasStoragePlugin {
    BaasStoragePlugin::new(
        BaasConfig { endpoint: server_url, tenant: "t".into(), ..Default::default() },
        5_000_000_000,
    )
}

#[tokio::test]
async fn health_check_ok_when_2xx() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(200))
        .mount(&server).await;
    let h: StorageHealth = plugin(server.uri()).health_check().await.unwrap();
    assert!(h.ok);
}

#[tokio::test]
async fn health_check_ok_on_404_405() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(404))
        .mount(&server).await;
    let h = plugin(server.uri()).health_check().await.unwrap();
    assert!(h.ok, "404 means reachable");
}

#[tokio::test]
async fn health_check_not_ok_on_500() {
    let server = MockServer::start().await;
    Mock::given(method("GET")).and(path("/"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server).await;
    let h = plugin(server.uri()).health_check().await.unwrap();
    assert!(!h.ok);
}

#[tokio::test]
async fn health_check_not_ok_when_unreachable() {
    // unreachable port
    let h = plugin("http://127.0.0.1:1".into()).health_check().await.unwrap();
    assert!(!h.ok);
}