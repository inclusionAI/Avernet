use super::fixture::Cli;
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{body_json, header, method, path, query_param}};

#[tokio::test]
async fn context_operations_preserve_scope_policies_and_explicit_auth() {
    let cli=Cli::new().await;
    let cases = [
        (vec!["add","--name","decision","--scope-level","group","--content","approved","--visible-to","{\"userIds\":[\"human-a\"]}","--collect-from","{\"userIds\":[\"bot-peer\"]}","--valid-from","123","--valid-to","2030-01-01T00:00:00Z"],
         json!({"name":"decision","scope_level":"group","content":"approved","visible_to":{"userIds":["human-a"]},"collect_from":{"userIds":["bot-peer"]},"valid_from":123,"valid_to":"2030-01-01T00:00:00Z"})),
        (vec!["update","--name","decision","--scope-level","group","--content","revised","--visible-to","{}","--collect-from","{}","--owners","{\"userIds\":[\"human-a\"]}","--valid-from","456","--valid-to","789","--change-reason","reviewed"],
         json!({"name":"decision","scope_level":"group","content":"revised","visible_to":{},"collect_from":{},"owners":{"userIds":["human-a"]},"valid_from":456,"valid_to":789,"change_reason":"reviewed"})),
        (vec!["list","--scope-levels","group,session"],json!({"scope_levels":["group","session"]})),
        (vec!["get","--name","decision","--scope-level","group","--limit","3"],json!({"name":"decision","scope_levels":["group"],"limit":3})),
    ];
    for (subargs,body) in cases {
        let response=json!({"status":"ok","result":subargs[0]});
        Mock::given(method("POST")).and(path(format!("/groupcontext/{}",subargs[0])))
            .and(query_param("groupId","g")).and(query_param("sessionId","s")).and(query_param("roundId","r"))
            .and(header("authorization","Bearer explicit-context-token"))
            .and(body_json(body)).respond_with(ResponseTemplate::new(200).set_body_json(&response)).expect(1).mount(&cli.server).await;
        let mut args=vec!["group-context","--auth","explicit-context-token"];
        args.extend(subargs);args.extend(["--group-id","g","--session-id","s","--round-id","r"]);
        let out=cli.ok(&[],&args);
        assert_eq!(serde_json::from_slice::<Value>(&out.stdout).unwrap(),response);
    }
}

#[tokio::test]
async fn context_validation_rejects_bad_policies_before_network_io() {
    let cli=Cli::new().await;
    for (option,value,needle) in [
        ("--visible-to","not-json","Invalid --visible-to JSON"),
        ("--visible-to","[]","--visible-to must be a JSON object"),
        ("--collect-from","not-json","Invalid --collect-from JSON"),
        ("--owners","not-json","Invalid --owners JSON"),
        ("--owners","{}","--owners must be a JSON object containing"),
        ("--owners","{\"userIds\":[]}","--owners.userIds must not be empty"),
        ("--owners","{\"userIds\":[\" \"]}","--owners.userIds must contain non-empty"),
    ] {
        cli.fail(&[],&["group-context","update","--group-id","g","--name","decision","--scope-level","group","--change-reason","review",option,value],needle);
    }
    for operation in ["list","get"] {
        let mut args=vec!["group-context",operation,"--group-id","g","--scope-level","group","--scope-levels","session"];
        if operation=="get" { args.extend(["--name","decision"]); }
        cli.fail(&[],&args,"--scope-level and --scope-levels cannot be used together");
    }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test]
async fn context_backend_error_is_not_reported_as_success() {
    let cli=Cli::new().await;
    Mock::given(method("POST")).and(path("/groupcontext/list"))
        .and(body_json(json!({"scope_levels":null})))
        .respond_with(ResponseTemplate::new(403).set_body_string("permission denied")).expect(1).mount(&cli.server).await;
    cli.fail(&[],&["group-context","list","--group-id","g"],"403");
}
