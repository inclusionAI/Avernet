use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{header, method, path}};

#[tokio::test]
async fn office_auth_failure_stops_dispatch_in_every_output_mode() {
    for flags in [&[][..], &["--json"][..], &["--no-json"][..]] {
        let cli=Cli::new().await;
        let mut command=cli.command(flags,&["health"]);
        command.env("tc_sdb_nenv","office").env("BCS_OAUTH_CLIENT_ID","test-client-id")
            .env("BCS_OAUTH_CLIENT_SECRET","test-client-secret");
        let assertion=if flags==["--no-json"] { command.assert().failure().code(1) } else { command.assert().success() };
        let out=assertion.get_output();
        if flags==["--no-json"] {
            assert!(out.stdout.is_empty());
            assert!(String::from_utf8_lossy(&out.stderr).contains("not available in the public build"));
        } else {
            let response:Value=serde_json::from_slice(&out.stdout).unwrap();
            assert_eq!(response["status"],"auth_failed");
            assert!(response["message"].as_str().unwrap().contains("not available in the public build"));
        }
        assert!(cli.server.received_requests().await.unwrap().is_empty(),"auth failure must not dispatch health");
    }
}

#[tokio::test]
async fn command_factory_preserves_explicit_token_cookie_and_actor_headers() {
    let cli=Cli::new().await;
    Mock::given(method("GET")).and(path("/sessions/s"))
        .and(header("authorization","Bearer explicit-token"))
        .and(header("cookie","test-cookie=present"))
        .and(header("x-bcn-actor-bot-id","bot-actor"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"session_id":"s"})))
        .expect(1).mount(&cli.server).await;
    let out=cli.command(&["--json","--cookie","test-cookie=present"],&["session","--token","explicit-token","get","s"])
        .env("BCN_ACTOR_BOT_ID","bot-actor").assert().success().get_output().clone();
    assert_eq!(serde_json::from_slice::<Value>(&out.stdout).unwrap()["session_id"],"s");
}

#[tokio::test]
async fn debug_health_emits_diagnostics_without_changing_json_stdout() {
    let cli=Cli::new().await;
    Mock::given(method("GET")).and(path("/health"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"status":"ok"})))
        .expect(1).mount(&cli.server).await;
    let out=cli.command(&["--json","--log-level","debug"],&["health"])
        .env("BCS_DEBUG","true").assert().success().get_output().clone();
    assert_eq!(serde_json::from_slice::<Value>(&out.stdout).unwrap()["status"],"healthy");
    if cfg!(debug_assertions) {
        let err=String::from_utf8_lossy(&out.stderr);
        assert!(err.contains("Environment:")); assert!(err.contains("Network: Prod"));
    }
    assert!(!stdout(&out).contains("[→BCS]"));
}
