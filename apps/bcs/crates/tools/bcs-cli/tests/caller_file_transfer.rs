use bcs_cli::BcsClient;
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, task::JoinHandle};

async fn server(response: String) -> (String, JoinHandle<String>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}",listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        let (mut socket,_) = listener.accept().await.unwrap();
        let mut bytes=vec![0;8192]; let count=socket.read(&mut bytes).await.unwrap();
        socket.write_all(response.as_bytes()).await.unwrap();
        String::from_utf8_lossy(&bytes[..count]).to_lowercase()
    });
    (url,task)
}
fn client(url: &str) -> BcsClient {
    let mut client=BcsClient::with_token_and_cookie(url,"private-bot-token","private-cookie");
    client.set_actor_id(Some("bot-a")).unwrap(); client
}
fn assert_no_identity(request: &str) {
    for header in ["authorization:","cookie:","x-bcn-actor-bot-id:","x-bcs-bot-token:"] {
        assert!(!request.contains(header),"storage received {header}");
    }
}

#[tokio::test]
async fn same_host_different_port_upload_has_no_bcn_identity() {
    let (storage,task)=server("HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into()).await;
    client("http://127.0.0.1:1").put_session_file_bytes(&storage,"data".into(),"text/plain").await.unwrap();
    assert_no_identity(&task.await.unwrap());
}

#[tokio::test]
async fn presigned_download_follows_redirect_without_bcn_identity() {
    let (storage,storage_task)=server("HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\ndata".into()).await;
    let (bcn,bcn_task)=server(format!("HTTP/1.1 302 Found\r\nLocation: {storage}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")).await;
    let dir=tempfile::tempdir().unwrap(); let file=dir.path().join("download");
    client(&bcn).download_session_file("s","f",file.to_str(),None).await.unwrap();
    assert_eq!(std::fs::read(file).unwrap(),b"data");
    assert!(bcn_task.await.unwrap().contains("x-bcn-actor-bot-id: bot-a"));
    assert_no_identity(&storage_task.await.unwrap());
}

#[test]
fn download_waits_for_file_writes_to_complete() {
    use std::time::Duration;

    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build().unwrap()
        .block_on(async {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            let (send_body, body_ready) = tokio::sync::oneshot::channel();
            let server = tokio::spawn(async move {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                let mut chunk = [0; 1024];
                while !request.windows(4).any(|part| part == b"\r\n\r\n") {
                    let count = socket.read(&mut chunk).await.unwrap();
                    assert_ne!(count, 0, "request ended before headers");
                    request.extend_from_slice(&chunk[..count]);
                }
                socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\n").await.unwrap();
                body_ready.await.unwrap();
                socket.write_all(b"data").await.unwrap();
            });
            let dir = tempfile::tempdir().unwrap();
            let file = dir.path().join("download");
            let out = file.clone();
            let bcs = client(&url);
            let mut download = tokio::spawn(async move {
                bcs.download_session_file("s", "f", out.to_str(), None).await
            });
            tokio::time::timeout(Duration::from_secs(10), async {
                while !file.exists() {
                    tokio::time::sleep(Duration::from_millis(1)).await;
                }
            }).await.expect("download must create the output file before receiving the body");

            // Hold the only blocking worker after file creation. The response
            // body can be received, but its filesystem write cannot finish yet.
            let (started, worker_started) = tokio::sync::oneshot::channel();
            let (release, released) = std::sync::mpsc::channel();
            let worker = tokio::task::spawn_blocking(move || {
                started.send(()).unwrap();
                let _ = released.recv();
            });
            worker_started.await.unwrap();
            send_body.send(()).unwrap();
            let early_result = tokio::time::timeout(Duration::from_millis(100), &mut download).await;
            let bytes_before_write = std::fs::read(&file).unwrap();
            // Always unblock the worker before asserting, including on failure.
            release.send(()).unwrap();
            worker.await.unwrap();
            assert!(early_result.is_err(),
                "download returned before its file write completed: {early_result:?}, bytes: {bytes_before_write:?}");
            tokio::time::timeout(Duration::from_secs(10), download).await
                .expect("download must finish after releasing the writer").unwrap().unwrap();
            assert_eq!(std::fs::read(&file).unwrap(), b"data");
            server.await.unwrap();
        });
}
