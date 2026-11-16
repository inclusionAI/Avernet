//! Assert that the HTTP adapter does not import service-api core internals.

use std::fs;

#[test]
fn adapter_does_not_import_core() {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let src_dir = std::path::Path::new(manifest_dir).join("src");
    let mut violations = Vec::new();

    visit(&src_dir, &mut violations);

    assert!(
        violations.is_empty(),
        "R7 violation: adapter imports bcs_service_api::core in:\n{}",
        violations.join("\n")
    );
}

fn visit(dir: &std::path::Path, out: &mut Vec<String>) {
    for entry in fs::read_dir(dir).expect("read dir") {
        let path = entry.expect("entry").path();
        if path.is_dir() {
            visit(&path, out);
            continue;
        }
        if path.extension().and_then(|e| e.to_str()) != Some("rs") {
            continue;
        }

        let src = fs::read_to_string(&path).expect("read source");
        for (lineno, line) in src.lines().enumerate() {
            let forbidden = ["use ", "bcs_service_api", "::", "core"].concat();
            if line.contains(&forbidden) {
                out.push(format!("{}:{}: {}", path.display(), lineno + 1, line.trim()));
            }
        }
    }
}
