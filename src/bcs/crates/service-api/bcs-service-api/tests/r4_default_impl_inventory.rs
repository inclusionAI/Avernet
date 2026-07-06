//! Assert that core trait files keep default implementation bodies small.

use std::fs;

const CORE_DIR: &str = "src/core";
const MAX_DEFAULT_BODY_LINES: usize = 200;

#[test]
fn core_trait_default_impl_body_within_budget() {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let core_path = std::path::Path::new(manifest_dir).join(CORE_DIR);
    let mut total = 0;

    for entry in fs::read_dir(&core_path).expect("read core dir") {
        let path = entry.expect("entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }
        let content = fs::read_to_string(&path).expect("read core file");
        total += count_default_impl_lines(&content);
    }

    assert!(
        total <= MAX_DEFAULT_BODY_LINES,
        "core trait default impl bodies = {total} lines (budget: {MAX_DEFAULT_BODY_LINES}). Move business logic to application use cases."
    );
}

fn count_default_impl_lines(src: &str) -> usize {
    let mut in_default = false;
    let mut depth = 0usize;
    let mut count = 0usize;

    for line in src.lines() {
        let trimmed = line.trim();
        if !in_default && trimmed.starts_with("async fn") && trimmed.ends_with('{') {
            in_default = true;
            depth = 1;
            continue;
        }
        if in_default {
            depth += trimmed.matches('{').count();
            depth -= trimmed.matches('}').count();
            if depth == 0 {
                in_default = false;
            } else {
                count += 1;
            }
        }
    }

    count
}
