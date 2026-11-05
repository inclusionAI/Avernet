use bcs_tracing_api::{Call, Kind, Outcome, RequestInfo};

pub trait BlockingRequestBuilderExt {
    fn send_traced(self) -> reqwest::Result<reqwest::blocking::Response>;
}

impl BlockingRequestBuilderExt for reqwest::blocking::RequestBuilder {
    fn send_traced(self) -> reqwest::Result<reqwest::blocking::Response> {
        let (client, request) = self.build_split();
        let mut request = request?;
        if !bcs_tracing_api::is_enabled() { return client.execute(request); }
        let mut endpoint = request.url().clone();
        let _ = endpoint.set_username("");
        let _ = endpoint.set_password(None);
        endpoint.set_query(None);
        endpoint.set_fragment(None);
        let incoming = request.headers().iter().filter_map(|(name, value)| {
            value.to_str().ok().map(|value| (name.to_string(), value.to_owned()))
        }).collect();
        let call = Call::start(&RequestInfo {
            kind: Kind::HttpClient, method: request.method().as_str(), endpoint: endpoint.as_str(),
            route: None, target_app: "", request_size: request.body().and_then(|body| body.as_bytes()).map(|bytes| bytes.len() as u64),
        }, &incoming);
        for (name, value) in call.headers() {
            if let (Ok(name), Ok(value)) = (reqwest::header::HeaderName::from_bytes(name.as_bytes()), reqwest::header::HeaderValue::from_str(value)) {
                request.headers_mut().insert(name, value);
            }
        }
        let result = client.execute(request);
        match &result {
            Ok(response) => call.finish(Outcome::Http(response.status().as_u16()), response.content_length()),
            Err(error) => call.finish(if error.is_timeout() { Outcome::Timeout } else { Outcome::TransportError }, None),
        }
        result
    }
}
