use bcs_a2a_http::text::{ArtifactText, TextProjectionError as Error};

#[test]
fn text_tool_text_and_final_snapshot_do_not_duplicate_prefix() {
    let mut artifact = ArtifactText::default();
    assert_eq!(artifact.update("我先查询", false, false), Ok("我先查询".into()));
    // BCS flushes a segment at the tool boundary; Artifact state remains alive.
    assert_eq!(artifact.update("结果成功", true, false), Ok("结果成功".into()));
    assert_eq!(artifact.update("我先查询结果成功！", false, true), Ok("！".into()));
    assert_eq!(artifact.snapshot("我先查询结果成功！"), Ok("".into()));
}

#[test]
fn intermediate_replacements_are_rejected_even_if_equal_or_prefix_extensions() {
    for replacement in ["hello", "hello world", "changed", ""] {
        let mut artifact = ArtifactText::default();
        artifact.update("hello", false, false).unwrap();
        let before = artifact.clone();
        assert_eq!(artifact.update(replacement, false, false), Err(Error::IntermediateReplacement));
        assert_eq!(artifact, before);
    }
}

#[test]
fn repeated_words_are_valid_deltas_and_whitespace_is_preserved() {
    let mut artifact = ArtifactText::default();
    artifact.update("好 ", false, false).unwrap();
    assert_eq!(artifact.update("好 ", true, true), Ok("好 ".into()));
    assert_eq!(artifact.text(), "好 好 ");
    assert_eq!(artifact.update("好 好 ", false, true), Ok("".into()));
    assert_eq!(artifact.update("late", true, false), Err(Error::ArtifactAlreadyFinished));
}

#[test]
fn recovery_only_appends_missing_suffix_and_rejects_revisions_atomically() {
    let mut artifact = ArtifactText::default();
    assert_eq!(artifact.update("unknown", true, false), Err(Error::MissingBaseline));
    artifact.snapshot("你好 ").unwrap();
    assert_eq!(artifact.snapshot("你好 世界"), Ok("世界".into()));
    let before = artifact.clone();
    assert_eq!(artifact.snapshot("你好"), Err(Error::RevisionUnsupported));
    assert_eq!(artifact.update("改写", false, true), Err(Error::RevisionUnsupported));
    assert_eq!(artifact, before);
}

#[test]
fn independent_artifacts_and_empty_final_are_supported() {
    let mut first = ArtifactText::default();
    let mut second = ArtifactText::default();
    first.update("one", false, true).unwrap();
    assert_eq!(second.update("", false, true), Ok("".into()));
    assert_eq!(first.text(), "one");
}
