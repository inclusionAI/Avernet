use bcs_tracing_api::{Call, Kind, Outcome, RequestInfo, with_context};

pub trait RequestBuilderExt {
    type Response;
    type Error;
    fn send_traced(self) -> impl Future<Output = Result<Self::Response, Self::Error>> + Send;
}

pub trait ClientExt {
    type Request;
    type Response;
    type Error;
    fn execute_traced(&self, request: Self::Request) -> impl Future<Output = Result<Self::Response, Self::Error>> + Send;
}

// BCS uses reqwest 0.12; the public A2A SDK uses 0.13. Keep the wire hook
// identical without forcing either client's TLS/runtime dependency to change.
macro_rules! client_adapter {
    ($module:ident, $client:ident) => {
        mod $module {
            use super::*;
            use $client::{Client, Request, RequestBuilder, Response};

            impl RequestBuilderExt for RequestBuilder {
                type Response = Response;
                type Error = $client::Error;
                async fn send_traced(self) -> $client::Result<Response> {
                    let (client, request) = self.build_split();
                    execute(&client, request?).await
                }
            }

            impl ClientExt for Client {
                type Request = Request;
                type Response = Response;
                type Error = $client::Error;
                async fn execute_traced(&self, request: Request) -> $client::Result<Response> {
                    execute(self, request).await
                }
            }

            async fn execute(client: &Client, mut request: Request) -> $client::Result<Response> {
                if !bcs_tracing_api::is_enabled() { return client.execute(request).await; }
                let mut endpoint = request.url().clone();
                let _ = endpoint.set_username("");
                let _ = endpoint.set_password(None);
                endpoint.set_query(None);
                endpoint.set_fragment(None);
                let call = Call::start(&RequestInfo {
                    kind: Kind::HttpClient, method: request.method().as_str(), endpoint: endpoint.as_str(),
                    target_app: "", request_size: request.body().and_then(|body| body.as_bytes()).map(|bytes| bytes.len() as u64),
                }, &Vec::new());
                for (name, value) in call.headers() {
                    if let (Ok(name), Ok(value)) = ($client::header::HeaderName::from_bytes(name.as_bytes()), $client::header::HeaderValue::from_str(value)) {
                        request.headers_mut().insert(name, value);
                    }
                }
                let result = with_context(call.context(), client.execute(request)).await;
                match &result {
                    Ok(response) => call.finish(Outcome::Http(response.status().as_u16()), response.content_length()),
                    Err(error) => call.finish(if error.is_timeout() { Outcome::Timeout } else { Outcome::TransportError }, None),
                }
                result
            }
        }
    };
}

client_adapter!(v12, reqwest);
#[cfg(feature = "reqwest13")]
client_adapter!(v13, reqwest13);
