use std::{
    collections::{BTreeSet, HashSet},
    fs,
    path::Path,
};

use syn::{
    File, Ident, ItemExternCrate, ItemUse, Path as SynPath, UseTree,
    visit::{self, Visit},
};

const DISALLOWED_ROOT_WIRE_DTO_NAMES: &[&str] = &["BotInfo"];

fn rust_files_under(dir: &Path, files: &mut Vec<std::path::PathBuf>) {
    for entry in fs::read_dir(dir).expect("read source directory") {
        let entry = entry.expect("read source entry");
        let path = entry.path();
        if path.is_dir() {
            rust_files_under(&path, files);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            files.push(path);
        }
    }
}

fn ident_is(ident: &Ident, name: &str) -> bool {
    ident == name
}

fn is_crate_root_prefix(prefix: &[String]) -> bool {
    prefix.len() == 1 && prefix[0] == "crate"
}

fn use_tree_starts_with_any(tree: &UseTree, names: &HashSet<String>) -> bool {
    match tree {
        UseTree::Path(path) => {
            names.contains(&path.ident.to_string()) || use_tree_starts_with_any(&path.tree, names)
        }
        UseTree::Group(group) => group
            .items
            .iter()
            .any(|item| use_tree_starts_with_any(item, names)),
        UseTree::Name(name) => names.contains(&name.ident.to_string()),
        UseTree::Rename(rename) => names.contains(&rename.ident.to_string()),
        UseTree::Glob(_) => false,
    }
}

fn use_tree_imports_root_wire_reexport(tree: &UseTree, prefix: &mut Vec<String>) -> bool {
    match tree {
        UseTree::Path(path) => {
            prefix.push(path.ident.to_string());
            let found = use_tree_imports_root_wire_reexport(&path.tree, prefix);
            prefix.pop();
            found
        }
        UseTree::Name(name) => {
            is_crate_root_prefix(prefix)
                && DISALLOWED_ROOT_WIRE_DTO_NAMES.contains(&name.ident.to_string().as_str())
        }
        UseTree::Rename(rename) => {
            is_crate_root_prefix(prefix)
                && DISALLOWED_ROOT_WIRE_DTO_NAMES.contains(&rename.ident.to_string().as_str())
        }
        UseTree::Glob(_) => is_crate_root_prefix(prefix),
        UseTree::Group(group) => group
            .items
            .iter()
            .any(|item| use_tree_imports_root_wire_reexport(item, prefix)),
    }
}

fn path_starts_with_any(path: &SynPath, names: &HashSet<String>) -> bool {
    path.segments
        .first()
        .is_some_and(|segment| names.contains(&segment.ident.to_string()))
}

fn path_is_root_wire_reexport(path: &SynPath) -> bool {
    let mut segments = path.segments.iter();
    let Some(first) = segments.next() else {
        return false;
    };
    let Some(second) = segments.next() else {
        return false;
    };

    ident_is(&first.ident, "crate")
        && DISALLOWED_ROOT_WIRE_DTO_NAMES.contains(&second.ident.to_string().as_str())
}

#[derive(Default)]
struct ProtocolAliasCollector {
    aliases: HashSet<String>,
}

impl<'ast> Visit<'ast> for ProtocolAliasCollector {
    fn visit_item_extern_crate(&mut self, node: &'ast ItemExternCrate) {
        if ident_is(&node.ident, "bcs_protocol") {
            let alias = node
                .rename
                .as_ref()
                .map(|(_, ident)| ident.to_string())
                .unwrap_or_else(|| "bcs_protocol".to_string());
            self.aliases.insert(alias);
        }

        visit::visit_item_extern_crate(self, node);
    }
}

struct BoundaryVisitor {
    protocol_names: HashSet<String>,
    offenses: BTreeSet<String>,
}

impl BoundaryVisitor {
    fn new(protocol_aliases: HashSet<String>) -> Self {
        let mut protocol_names = protocol_aliases;
        protocol_names.insert("bcs_protocol".to_string());
        Self {
            protocol_names,
            offenses: BTreeSet::new(),
        }
    }

    fn add_offense(&mut self, reason: impl Into<String>) {
        self.offenses.insert(reason.into());
    }
}

impl<'ast> Visit<'ast> for BoundaryVisitor {
    fn visit_item_extern_crate(&mut self, node: &'ast ItemExternCrate) {
        if ident_is(&node.ident, "bcs_protocol") {
            self.add_offense("extern crate bcs_protocol".to_string());
        }

        visit::visit_item_extern_crate(self, node);
    }

    fn visit_item_use(&mut self, node: &'ast ItemUse) {
        if use_tree_starts_with_any(&node.tree, &self.protocol_names) {
            self.add_offense("use of bcs_protocol or an alias".to_string());
        }
        if use_tree_imports_root_wire_reexport(&node.tree, &mut Vec::new()) {
            self.add_offense("use of crate root wire DTO re-export".to_string());
        }

        visit::visit_item_use(self, node);
    }

    fn visit_path(&mut self, node: &'ast SynPath) {
        if path_starts_with_any(node, &self.protocol_names) {
            self.add_offense("path through bcs_protocol or an alias".to_string());
        }
        if path_is_root_wire_reexport(node) {
            self.add_offense("path through crate root wire DTO re-export".to_string());
        }

        visit::visit_path(self, node);
    }
}

fn source_offenses(source: &str) -> BTreeSet<String> {
    let parsed: File = syn::parse_file(source).expect("parse rust source");
    let mut aliases = ProtocolAliasCollector::default();
    aliases.visit_file(&parsed);

    let mut visitor = BoundaryVisitor::new(aliases.aliases);
    visitor.visit_file(&parsed);
    visitor.offenses
}

#[test]
fn application_and_core_do_not_import_bcs_protocol() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    rust_files_under(&manifest_dir.join("src/application"), &mut files);
    rust_files_under(&manifest_dir.join("src/core"), &mut files);

    let offenders: Vec<_> = files
        .into_iter()
        .filter_map(|path| {
            let source = fs::read_to_string(&path).expect("read source file");
            let offenses = source_offenses(&source);
            if offenses.is_empty() {
                None
            } else {
                Some(format!(
                    "{}: {:?}",
                    path.strip_prefix(manifest_dir).unwrap().display(),
                    offenses
                ))
            }
        })
        .collect();

    assert!(
        offenders.is_empty(),
        "application/core must not import bcs_protocol or root wire DTO re-exports: {offenders:?}"
    );
}

#[test]
fn crate_root_does_not_reexport_wire_dtos() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(manifest_dir.join("src/lib.rs")).expect("read crate root");

    for name in DISALLOWED_ROOT_WIRE_DTO_NAMES {
        let direct = format!("pub use bcs_protocol::{name}");
        let grouped = format!("pub use bcs_protocol::{{{name}");
        assert!(
            !source.contains(&direct) && !source.contains(&grouped),
            "crate root must not re-export wire DTO {name}"
        );
    }
}

#[test]
fn authenticated_caller_contract_contains_no_transport_or_credential_fields() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = match fs::read_to_string(manifest_dir.join("src/application/v1/identity.rs")) {
        Ok(source) => source,
        Err(_) => panic!("read authenticated identity contract"),
    };

    for forbidden in [
        "jsonwebtoken",
        "axum",
        "HeaderMap",
        "X-Avernet-Principal",
        "access_key_token",
        "bot_token",
    ] {
        assert!(
            !source.contains(forbidden),
            "authenticated identity contract must not contain {forbidden}",
        );
    }
}

#[test]
fn boundary_scan_ignores_comments_and_string_literals() {
    let offenses = source_offenses(
        r#"
        // use bcs_protocol::BotInfo;
        const DOC: &str = "bcs_protocol::BotInfo";
        pub struct Local;
        "#,
    );

    assert!(offenses.is_empty());
}

#[test]
fn boundary_scan_catches_protocol_paths_and_aliases() {
    let offenses = source_offenses(
        r#"
        extern crate bcs_protocol as protocol;

        fn direct(_: bcs_protocol :: BotInfo) {}
        fn aliased(_: protocol::BotInfo) {}
        "#,
    );

    assert!(offenses.contains("extern crate bcs_protocol"));
    assert!(offenses.contains("path through bcs_protocol or an alias"));
}

#[test]
fn boundary_scan_catches_root_wire_reexports() {
    let offenses = source_offenses(
        r#"
        use crate::{BotInfo as WireBotInfo};

        fn root(_: crate::BotInfo) {}
        fn aliased(_: WireBotInfo) {}
        "#,
    );

    assert!(offenses.contains("use of crate root wire DTO re-export"));
    assert!(offenses.contains("path through crate root wire DTO re-export"));
}
