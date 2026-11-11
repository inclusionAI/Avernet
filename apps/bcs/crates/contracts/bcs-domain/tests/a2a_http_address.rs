use bcs_domain::a2a::{http_interface_address_allowed, select_a2a_interface, A2aBinding};
use serde_json::json;

#[test]
fn http_and_https_interfaces_are_not_limited_to_loopback() {
    for address in ["agent.example.org", "10.0.0.1", "127.0.0.1", "localhost", "[::1]", "[fd00::1]"] {
        for scheme in ["http", "https"] {
            let url = format!("{scheme}://{address}:9000/a2a");
            assert!(http_interface_address_allowed(&url), "{url}");
            for (binding, expected) in [("HTTP+JSON", A2aBinding::HttpJson), ("JSONRPC", A2aBinding::JsonRpc)] {
                let card = json!({"supportedInterfaces":[{"url":url,
                    "protocolBinding":binding,"protocolVersion":"1.0"}]});
                let selected = select_a2a_interface(&card, |_, url| http_interface_address_allowed(url)).unwrap();
                assert_eq!(selected.url, url);
                assert_eq!(selected.binding, expected);
            }
        }
    }
}

#[test]
fn unsafe_and_non_http_interface_addresses_remain_rejected() {
    for url in ["", "/a2a", "not a URL", "http://", "https://", "file:///a2a", "ftp://example.org/a2a",
        "http://user@example.org/a2a", "https://user:password@example.org/a2a",
        "http://example.org/a2a#fragment", "https://example.org/a2a#fragment",
        " http://example.org/a2a", "https://example.org/a2a "] {
        assert!(!http_interface_address_allowed(url), "{url}");
    }
}
