use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{body_json, body_bytes, header, method, path, query_param}};

#[tokio::test]
async fn file_upload_download_and_management_preserve_bytes_requests_and_modes() {
    for flags in [&[][..], &["--json"][..]] {
        let cli = Cli::new().await;
        let input = cli.home.path().join("upload.bin");
        let output = cli.home.path().join("download.bin");
        let content = b"\x00contract\xff";
        std::fs::write(&input, content).unwrap();
        Mock::given(method("POST")).and(path("/sessions/s/files"))
            .and(header("authorization", "Bearer test-command-token"))
            .and(body_json(json!({"file_name":"report.bin","size":content.len(),"mime_type":"application/octet-stream"})))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"file_id":"f","upload_url":format!("{}/upload",cli.server.uri())})))
            .expect(1).mount(&cli.server).await;
        Mock::given(method("PUT")).and(path("/upload")).and(body_bytes(content.to_vec()))
            .and(header("content-type", "application/octet-stream"))
            .respond_with(ResponseTemplate::new(200)).expect(1).mount(&cli.server).await;
        let file = json!({"file_id":"f","file_name":"report.bin","size":content.len(),"status":"Ready"});
        Mock::given(method("POST")).and(path("/sessions/s/files/f/complete")).and(body_json(json!({})))
            .respond_with(ResponseTemplate::new(200).set_body_json(&file)).expect(1).mount(&cli.server).await;
        let uploaded = cli.command(flags, &["session","file","upload","--session","s","--path",input.to_str().unwrap(),"--name","report.bin","--mime","application/octet-stream"])
            .env("BCS_DEBUG","true").assert().success().get_output().clone();
        if flags.is_empty() { assert!(stdout(&uploaded).contains("Uploaded: f (10)")); }
        else { assert_eq!(serde_json::from_slice::<Value>(&uploaded.stdout).unwrap(), file); }

        Mock::given(method("GET")).and(path("/sessions/s/files"))
            .and(query_param("prefix","report")).and(query_param("status","Ready"))
            .and(query_param("limit","5")).and(query_param("offset","0"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"items":[file],"total":1})))
            .expect(1).mount(&cli.server).await;
        let listing = cli.ok(flags, &["session","file","list","--session","s","--prefix","report","--status","Ready","--limit","5","--offset","0"]);
        if flags.is_empty() { assert!(stdout(&listing).contains("f \"report.bin\" (10 bytes) [Ready]")); }
        else { assert_eq!(serde_json::from_slice::<Value>(&listing.stdout).unwrap()["items"][0]["file_id"],"f"); }

        Mock::given(method("GET")).and(path("/sessions/s/files/f/content")).and(query_param("ttl","60"))
            .respond_with(ResponseTemplate::new(200).set_body_bytes(content.to_vec())).expect(1).mount(&cli.server).await;
        let downloaded = cli.ok(flags, &["session","file","download","--session","s","--file-id","f","--out",output.to_str().unwrap(),"--ttl","60"]);
        assert_eq!(std::fs::read(&output).unwrap(),content);
        if flags.is_empty() { assert!(stdout(&downloaded).contains(&format!("Downloaded to {}",output.display()))); }
        else { assert_eq!(serde_json::from_slice::<Value>(&downloaded.stdout).unwrap()["out"],output.to_str().unwrap()); }

        for (args, verb, endpoint, body, response, needle) in [
            (vec!["session","file","share","--session","s","--file-id","f","--ttl","60"], "POST", "/sessions/s/files/f/share", Some(json!({"ttl_seconds":60})), json!({"share_url":"https://example.test/share","expires_at":123}), "https://example.test/share"),
            (vec!["session","file","capabilities","--session","s"], "GET", "/sessions/s/files/capabilities", None, json!({"storage":"local"}), "local"),
            (vec!["session","file","delete","--session","s","--file-id","f"], "DELETE", "/sessions/s/files/f", None, json!({"deleted":true}), "Deleted: f"),
        ] {
            let mut mock = Mock::given(method(verb)).and(path(endpoint)).and(header("authorization","Bearer test-command-token"));
            if let Some(body)=body { mock=mock.and(body_json(body)); }
            mock.respond_with(ResponseTemplate::new(200).set_body_json(&response)).expect(1).mount(&cli.server).await;
            let out=cli.command(flags,&args).env("BCS_DEBUG","true").assert().success().get_output().clone();
            if flags.is_empty() { assert!(stdout(&out).contains(needle),"{}",stdout(&out)); }
            else { assert_eq!(serde_json::from_slice::<Value>(&out.stdout).unwrap(),response); }
        }
    }
}

#[tokio::test]
async fn file_permission_failures_propagate_to_cli() {
    let cli=Cli::new().await;
    for (args,verb,endpoint,needle) in [
        (vec!["session","file","list","--session","s"],"GET","/sessions/s/files","list session files"),
        (vec!["session","file","delete","--session","s","--file-id","f"],"DELETE","/sessions/s/files/f","delete session file"),
        (vec!["session","file","share","--session","s","--file-id","f"],"POST","/sessions/s/files/f/share","share session file"),
    ] {
        Mock::given(method(verb)).and(path(endpoint)).respond_with(ResponseTemplate::new(403).set_body_string("denied"))
            .expect(1).mount(&cli.server).await;
        cli.fail(&["--json"],&args,needle);
    }
}
