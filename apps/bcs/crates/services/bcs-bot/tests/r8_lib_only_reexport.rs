//! Asserts that lib.rs contains no struct or impl definitions.

#[test]
fn lib_rs_contains_only_reexports() {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let lib = std::path::Path::new(manifest_dir)
        .join("src")
        .join("lib.rs");
    let src = std::fs::read_to_string(&lib).expect("read lib.rs");

    let bad: Vec<_> = src
        .lines()
        .enumerate()
        .filter(|(_, line)| {
            let trimmed = line.trim_start();
            trimmed.starts_with("pub struct ")
                || trimmed.starts_with("struct ")
                || trimmed.starts_with("pub impl ")
                || trimmed.starts_with("impl ")
        })
        .collect();

    assert!(
        bad.is_empty(),
        "lib.rs has {} struct/impl line(s):\n{}",
        bad.len(),
        bad.iter()
            .map(|(n, l)| format!("{}: {}", n + 1, l))
            .collect::<Vec<_>>()
            .join("\n")
    );
}
