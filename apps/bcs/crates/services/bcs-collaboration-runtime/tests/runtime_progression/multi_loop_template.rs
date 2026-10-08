use super::*;

#[tokio::test]
async fn seeded_multiple_loops_keep_context_separate_and_complete_each_exit_path() {
    for (decisions, research_count, writing_count, exit) in [
        (vec!["revise", "approved", "revise", "approved"], 2, 2, "polish"),
        (vec!["revise", "revise", "revise"], 3, 0, "research_gaps"),
        (vec!["approved", "revise", "revise", "revise"], 1, 3, "rewrite"),
    ] {
        let h = Harness::new(&decisions).await;
        let mut definition: Value = serde_yaml::from_str(include_str!(
            "../../../../../seeds/collaboration-templates/zh-CN/research-writing-loops.yaml"
        )).unwrap();
        // The fixture runs all logical roles through its single recording Bot.
        // The shipped authoring YAML keeps all four bindings free of Bot IDs.
        for role in definition["participants"].as_object_mut().unwrap().values_mut() {
            role["bot_id"] = json!("driver-bot");
        }
        let started = h.start(serde_yaml::to_string(&definition).unwrap(), false).await;
        let run = &started.view.run;
        let plan = h.plan(&run.run_id).await;
        assert_eq!(started.view.nodes.len(), 16);
        let mut steps = Vec::new();
        for (loop_id, entry, count) in [("research_loop", "research", research_count), ("writing_loop", "draft", writing_count)] {
            for iteration in 1..=count {
                steps.push((Some(loop_id), Some(iteration), entry));
                steps.push((Some(loop_id), Some(iteration), "review"));
            }
        }
        steps.extend([(None, None, exit), (None, None, "finalize")]);
        let mut completed_ids = std::collections::BTreeSet::new();
        for (index, (loop_id, iteration, logical_id)) in steps.iter().copied().enumerate() {
            let meta = plan.node_metadata.values().find(|meta| meta.loop_id.as_deref() == loop_id
                && meta.iteration == iteration && meta.definition_node_id == logical_id).unwrap();
            let prompt = h.prompt(index).await;
            if meta.is_loop_entry {
                let loop_id = loop_id.unwrap();
                let iteration = iteration.unwrap();
                assert!(prompt.contains(&format!("loop_id: {loop_id}\niteration: {iteration}\n")), "{prompt}");
                if iteration == 1 {
                    assert!(prompt.contains("[Previous Iteration Result]\n(none - this is the first iteration)"), "{prompt}");
                    if loop_id == "writing_loop" {
                        let research = format!("research_loop-{research_count}-review-output");
                        assert_eq!(prompt.matches(&research).count(), 1, "{prompt}");
                    }
                } else {
                    let previous = format!("{loop_id}-{}-review-output", iteration - 1);
                    assert_eq!(prompt.matches(&previous).count(), 1, "{prompt}");
                }
            }
            let output = match (loop_id, iteration) {
                (Some(loop_id), Some(iteration)) => format!("{loop_id}-{iteration}-{logical_id}-output"),
                _ => format!("{logical_id}-output"),
            };
            h.finish(index, run, &output).await;
            let saved = h.store.get_node_run(&run.run_id, &meta.execution_node_id).await.unwrap().unwrap();
            assert_eq!(saved.status, StateMachineNodeStatus::Completed, "{exit}: {logical_id}");
            completed_ids.insert(meta.execution_node_id.clone());
        }
        assert_eq!(h.delivery.commands.lock().await.len(), steps.len());
        let finished = h.store.get_run(&run.run_id).await.unwrap().unwrap();
        assert_eq!(finished.status, StateMachineRunStatus::Completed);
        assert_eq!(finished.output.as_deref(), Some("finalize-output"));
        for node in h.store.list_node_runs(&run.run_id).await.unwrap() {
            assert_eq!(node.status, if completed_ids.contains(&node.node_id) {
                StateMachineNodeStatus::Completed
            } else { StateMachineNodeStatus::Skipped }, "{exit}: {}", node.node_id);
        }
    }
}
