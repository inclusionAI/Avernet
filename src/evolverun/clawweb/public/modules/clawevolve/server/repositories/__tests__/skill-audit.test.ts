import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase, runMigrations } from '@avernet/clawweb-shared/server/db';
import { SkillAssetRepository } from '../skill-asset-repository.js';
import { EvolveRepository } from '../evolve-repository.js';
import { StageSkillRepository } from '../stage-skill-repository.js';

describe('persisted Skill operation audit', () => {
  let db: SqliteDatabase;
  let assets: SkillAssetRepository;
  let tasks: EvolveRepository;
  const registration = { assetId: 'asset', versionId: 'v1', ownerUserId: 'owner', botId: 'bot',
    ocbSkillId: '44', displayName: 'Skill', description: 'actual description', packageRef: 'base', packageSha256: 'base-sha' };
  const task = { taskId: 'task', taskType: 'full', userId: 'owner', botId: 'bot', taskName: 'evolve',
    createdBy: 'operator', configJson: JSON.stringify({ targetSkill: { assetId: 'asset' } }) };
  beforeEach(async () => {
    db = new SqliteDatabase(new Database(':memory:'));
    await runMigrations(db, 'sqlite');
    assets = new SkillAssetRepository(db);
    tasks = new EvolveRepository(db);
  });
  afterEach(async () => { await db.close(); });

  it('freezes the explicitly selected Optimize score on the single optimization event', async () => {
    await assets.createAsset(registration);
    const config = { targetSkill: { assetId: 'asset', candidate: { artifact: { sha256: 'candidate' } } } };
    await tasks.createTask({ ...task, configJson: JSON.stringify(config) });
    for (const [stepId, round, score] of [['selected', 1, .7], ['later', 2, .99]] as const) {
      await tasks.createStep({ taskId: 'task', stepId, stepType: 'optimize', stepNo: round, roundNo: round, command: 'test' });
      await tasks.updateStepStatus(stepId, { status: 'succeeded', output: {
        scoreComparison: { name: 'test_score', baseline: .5, candidate: score, delta: score - .5 }, secret: 'not public',
      } });
    }
    await tasks.freezeSkillTestBenchSelection('task', 'selected');
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    const expected = { taskId: 'task', stepId: 'selected', round: 1,
      scoreComparison: { name: 'test_score', baseline: .5, candidate: .7, delta: .7 - .5 } };
    const original = (await assets.listEvents('owner')).find(event => event.event_type === 'optimization')!;
    expect(JSON.parse(original.detail_json!).testBench).toEqual(expected);
    // Later summaries/reports and repeated selection cannot rewrite this operation's evidence.
    await tasks.updateStepStatus('selected', { status: 'succeeded', output: { scoreComparison: { name: 'test_score', baseline: 0, candidate: 1, delta: 1 } } });
    await tasks.freezeSkillTestBenchSelection('task', 'selected');
    expect(await tasks.recordSkillDecision({ taskId: 'task', decision: 'accept', actorId: 'operator',
      expectedConfigJson: (await tasks.findTask('task'))!.config_json, candidateSha256: 'candidate' })).toBe(true);
    await assets.createAcceptedVersion({ assetId: 'asset', versionId: 'v2', sourceTaskId: 'task', packageRef: 'candidate',
      packageSha256: 'candidate-sha', baselinePackageRef: 'base', baselinePackageSha256: 'base-sha', appliedBy: 'operator' });
    const events = await assets.listEvents('owner');
    expect(events).toHaveLength(2);
    const optimization = events.find(event => event.event_type === 'optimization')!;
    expect(JSON.parse(optimization.detail_json!).testBench).toEqual(expected);
    expect(optimization).toMatchObject({ status: 'waiting_acceptance', outcome: 'applied', version_to_no: 2 });
    expect(events.find(event => event.event_type === 'registered')?.detail_json).toBeNull();
  });

  it('preserves the selected evidence through a failed finalize retry without rereading the producer', async () => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    await tasks.createStep({ taskId: 'task', stepId: 'selected', stepType: 'optimize', stepNo: 1, roundNo: 1, command: 'test' });
    await tasks.updateStepStatus('selected', { status: 'succeeded', output: { scoreComparison: { candidate: .75 } } });
    await tasks.freezeSkillTestBenchSelection('task', 'selected');
    await tasks.updateTaskState({ taskId: 'task', status: 'failed' });
    const config = JSON.parse((await tasks.findTask('task'))!.config_json);
    await tasks.prepareTaskRetry('task', config, 'operator');
    await tasks.updateStepStatus('selected', { status: 'succeeded', output: { scoreComparison: { candidate: 999 } } });
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    const event = (await assets.listEvents('owner')).find(item => item.event_type === 'optimization')!;
    expect(event.status).toBe('waiting_acceptance');
    expect(JSON.parse(event.detail_json!).testBench).toMatchObject({ stepId: 'selected', scoreComparison: { candidate: .75, delta: null } });
  });

  it('does not accept an arbitrary event-detail score as a selected producer', async () => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    await tasks.recordSkillOperation('task', { status: 'waiting_acceptance', outcome: 'version_apply_failed', actorType: 'system',
      detail: { errorCode: 'TEST', testBench: { taskId: 'task', stepId: 'invented', round: 1, scoreComparison: { candidate: 1 } } } });
    expect(JSON.parse((await assets.listEvents('owner'))[0].detail_json!)).toEqual({ errorCode: 'TEST' });
  });

  it.each(['other-task', 'failed', 'wrong-stage', 'zero-round', 'missing-run', 'foreign-owner', 'wrong-run-task', 'wrong-mode'] as const)
    ('rejects an unproven Optimize association before freezing: %s', async (kind) => {
      await assets.createAsset(registration);
      await tasks.createTask(task);
      await tasks.createTask({ ...task, taskId: 'other', configJson: '{}' });
      const extension = ['missing-run', 'foreign-owner', 'wrong-run-task', 'wrong-mode'].includes(kind);
      await tasks.createStep({ taskId: kind === 'other-task' ? 'other' : 'task', stepId: 'selected',
        stepType: extension ? 'stage_extension' : kind === 'wrong-stage' ? 'plan' : 'optimize', stepNo: 1,
        roundNo: kind === 'zero-round' ? 0 : 1, command: 'test' });
      if (extension && kind !== 'missing-run') {
        const stages = new StageSkillRepository(db);
        const mode = kind === 'wrong-mode' ? 'postprocess' : 'replace';
        await stages.createImplementation({ stageSkillId: 'skill', implementationId: 'impl', ownerUserId: kind === 'foreign-owner' ? 'another' : 'owner',
          displayName: 'Optimize', stage: 'optimize', mode, versionNo: 1, packageRef: 'pkg', packageSha256: 'sha', staticValidation: {} });
        await stages.createExtensionRun({ taskId: kind === 'wrong-run-task' ? 'other' : 'task', stepId: 'selected', stage: 'optimize', mode, implementationId: 'impl' });
      }
      await tasks.updateStepStatus('selected', { status: kind === 'failed' ? 'failed' : 'succeeded', output: { scoreComparison: { candidate: .75 } } });
      if (kind === 'failed') await tasks.updateTaskState({ taskId: 'task', status: 'running' });
      const before = (await tasks.findTask('task'))!.config_json;
      await expect(tasks.freezeSkillTestBenchSelection('task', 'selected')).rejects.toThrow('Optimize');
      expect((await tasks.findTask('task'))!.config_json).toBe(before);
    });

  it('does not backfill an old waiting task, even when an Optimize report already exists', async () => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    await tasks.createStep({ taskId: 'task', stepId: 'selected', stepType: 'optimize', stepNo: 1, roundNo: 1, command: 'test' });
    await tasks.updateStepStatus('selected', { status: 'succeeded', output: { scoreComparison: { candidate: .75 } } });
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    const before = await assets.listEvents('owner');
    await tasks.freezeSkillTestBenchSelection('task', 'selected');
    expect(JSON.parse((await tasks.findTask('task'))!.config_json).skillAuditTestBench).toBeUndefined();
    expect(await assets.listEvents('owner')).toEqual(before);
  });

  it('allows only one concurrent selected producer and carries its snapshot into rejection', async () => {
    await assets.createAsset(registration);
    await tasks.createTask({ ...task, configJson: JSON.stringify({ targetSkill: { assetId: 'asset', candidate: { artifact: { sha256: 'candidate' } } } }) });
    for (const [stepId, round] of [['first', 1], ['second', 2]] as const) {
      await tasks.createStep({ taskId: 'task', stepId, stepType: 'optimize', stepNo: round, roundNo: round, command: 'test' });
      await tasks.updateStepStatus(stepId, { status: 'succeeded', output: { scoreComparison: { candidate: .75 } } });
    }
    const outcomes = await Promise.allSettled([tasks.freezeSkillTestBenchSelection('task', 'first'),
      new EvolveRepository(db).freezeSkillTestBenchSelection('task', 'second')]);
    expect(outcomes.map(result => result.status)).toEqual(['fulfilled', 'rejected']);
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    expect(await tasks.recordSkillDecision({ taskId: 'task', decision: 'reject', actorId: 'operator',
      expectedConfigJson: (await tasks.findTask('task'))!.config_json })).toBe(true);
    expect(JSON.parse((await assets.listEvents('owner'))[0].detail_json!).testBench).toMatchObject({ stepId: 'first', round: 1 });
  });

  it.each(['accept', 'reject'] as const)('atomically excludes the opposite decision when %s wins across repository instances', async (first) => {
    await assets.createAsset(registration);
    const configJson = JSON.stringify({ targetSkill: { assetId: 'asset', candidate: { artifact: { sha256: 'candidate' } } } });
    await tasks.createTask({ ...task, configJson });
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    const input = { taskId: 'task', actorId: 'operator', expectedConfigJson: configJson, candidateSha256: 'candidate' };
    const other = new EvolveRepository(db);
    expect(await Promise.all([
      tasks.recordSkillDecision({ ...input, decision: first }),
      other.recordSkillDecision({ ...input, decision: first === 'accept' ? 'reject' : 'accept' }),
    ])).toEqual([true, false]);
    const event = (await assets.listEvents('owner')).find(item => item.event_type === 'optimization')!;
    expect(JSON.parse(event.detail_json!).decision).toBe(first);
    expect(event.outcome).toBe(first === 'accept' ? 'accepted' : 'rejected');
  });

  it('rolls back rejection and releases the decision lock when its audit fails', async () => {
    await assets.createAsset(registration);
    const configJson = JSON.stringify({ targetSkill: { assetId: 'asset', candidate: { artifact: { sha256: 'candidate' } } } });
    await tasks.createTask({ ...task, configJson });
    await tasks.updateTaskState({ taskId: 'task', status: 'waiting_acceptance' });
    await db.exec("CREATE TRIGGER fail_reject BEFORE UPDATE ON ce_skill_events WHEN NEW.outcome = 'rejected' BEGIN SELECT RAISE(ABORT, 'reject unavailable'); END");
    const input = { taskId: 'task', actorId: 'operator', expectedConfigJson: configJson };
    await expect(tasks.recordSkillDecision({ ...input, decision: 'reject' })).rejects.toThrow('reject unavailable');
    expect((await tasks.findTask('task'))?.status).toBe('waiting_acceptance');
    await db.exec('DROP TRIGGER fail_reject');
    expect(await tasks.recordSkillDecision({ ...input, decision: 'accept', candidateSha256: 'candidate' })).toBe(true);
    expect((await assets.listEvents('owner')).find(event => event.event_type === 'optimization')?.outcome).toBe('accepted');
  });

  it('allocates one version and applied event for concurrent acceptance retries', async () => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    const input = { assetId: 'asset', sourceTaskId: 'task', packageRef: 'candidate', packageSha256: 'candidate-sha',
      baselinePackageRef: 'base', baselinePackageSha256: 'base-sha', appliedBy: 'operator' };
    const versions = await Promise.all([
      assets.createAcceptedVersion({ ...input, versionId: 'v2-first' }),
      new SkillAssetRepository(db).createAcceptedVersion({ ...input, versionId: 'v2-second' }),
    ]);
    expect(versions[0]).toEqual(versions[1]);
    expect(await assets.listVersions('asset')).toHaveLength(2);
    expect((await assets.listEvents('owner')).filter(event => event.event_type === 'optimization')).toHaveLength(1);
    expect((await assets.listEvents('owner')).find(event => event.event_type === 'optimization')).toMatchObject({
      outcome: 'applied', version_to_no: 2,
    });
  });
  it('does not manufacture past events from existing version rows', async () => {
    await assets.createAsset(registration);
    await db.exec('DELETE FROM ce_skill_events');
    expect(await assets.listEvents('owner')).toEqual([]);
  });
  it('records registration/start/end once, with actor and actual result, scoped to the asset owner', async () => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    await tasks.completeTask('task', 'not_improved');
    await tasks.completeTask('task', 'not_improved');
    const events = await assets.listEvents('owner');
    expect(events.map(e => [e.event_type, e.status, e.outcome])).toEqual([
      ['optimization', 'completed', 'not_improved'], ['registered', 'completed', 'registered'],
    ]);
    expect(events[0]).toMatchObject({ asset_id: 'asset', task_id: 'task', actor_id: 'operator', actor_type: 'user' });
    expect(await assets.listEvents('bot-owner')).toEqual([]);
  });
  it('records Skill diagnosis separately and links the exact frozen baseline version', async () => {
    await assets.createAsset(registration);
    await tasks.createTask({ ...task, taskType: 'diagnose', configJson: JSON.stringify({
      targetSkill: { assetId: 'asset', baseline: { sha256: 'base-sha', versionId: 'v1', versionNo: 1 } },
    }) });
    await tasks.completeTask('task', 'no_cases');
    expect((await assets.listEvents('owner')).map(event => [
      event.event_type, event.status, event.outcome, event.version_from_id, event.version_from_no,
    ])).toEqual([
      ['diagnosis', 'completed', 'no_cases', 'v1', 1],
      ['registered', 'completed', 'registered', 'v1', 1],
    ]);
  });
  it('rolls back the business operation if audit persistence fails', async () => {
    await db.exec("CREATE TRIGGER reject_audit BEFORE INSERT ON ce_skill_events BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END");
    await expect(assets.createAsset(registration)).rejects.toThrow('audit unavailable');
    expect(await assets.findAsset('asset')).toBeNull();
  });

  it('keeps event snapshots immutable and rejects a cross-owner target at the write boundary', async () => {
    await assets.createAsset(registration);
    await db.exec("UPDATE ce_skill_assets SET display_name = 'renamed', description = 'changed' WHERE asset_id = 'asset'");
    expect((await assets.listEvents('owner'))[0]).toMatchObject({ display_name: 'Skill', description: 'actual description' });
    await expect(tasks.createTask({ ...task, userId: 'another-owner' })).rejects.toThrow('does not belong');
    expect(await tasks.findTask('task')).toBeNull();
    expect(await assets.listEvents('owner')).toHaveLength(1);
  });
  it('does not audit Stage integration tests as Skill evolution', async () => {
    await assets.createAsset(registration);
    await tasks.createTask({ ...task, taskType: 'stage_test' });
    await tasks.completeTask('task');
    expect((await assets.listEvents('owner')).map(e => e.event_type)).toEqual(['registered']);
  });

  it('rolls back task creation and finalization when business-event persistence fails', async () => {
    await assets.createAsset(registration);
    await db.exec("CREATE TRIGGER reject_audit BEFORE INSERT ON ce_skill_events BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END");
    await expect(tasks.createTask(task)).rejects.toThrow('audit unavailable');
    expect(await tasks.findTask('task')).toBeNull();
    await db.exec('DROP TRIGGER reject_audit');
    await tasks.createTask(task);
    await tasks.createStep({ stepId: 'finalize', taskId: 'task', stepType: 'skill_finalize', stepNo: 1, command: 'fixture' });
    await db.exec("CREATE TRIGGER reject_audit BEFORE UPDATE ON ce_skill_events BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END");
    const config = JSON.parse(task.configJson);
    await expect(tasks.updateStepStatus('finalize', { status: 'succeeded', taskState: { status: 'waiting_acceptance', config } })).rejects.toThrow('audit unavailable');
    expect((await tasks.findStep('finalize'))?.status).toBe('created');
    expect((await tasks.findTask('task'))?.status).toBe('pending');
    await db.exec('DROP TRIGGER reject_audit');
    await tasks.updateStepStatus('finalize', { status: 'succeeded', taskState: { status: 'waiting_acceptance', config } });
    expect((await assets.listEvents('owner'))[0].status).toBe('waiting_acceptance');
  });

  it.each(['failed', 'canceled'])('updates the same event through %s and a later retry', async (status) => {
    await assets.createAsset(registration);
    await tasks.createTask(task);
    await tasks.createStep({ stepId: 'step', taskId: 'task', stepType: 'diagnose', stepNo: 1, command: 'fixture' });
    await tasks.updateStepStatus('step', { status, errorCode: 'TEST_FAILURE' });
    await tasks.updateStepStatus('step', { status, errorCode: 'TEST_FAILURE' });
    expect((await assets.listEvents('owner')).find(e => e.event_type === 'optimization')).toMatchObject({ status, outcome: status });
    await tasks.prepareTaskRetry('task', JSON.parse(task.configJson));
    await tasks.completeTask('task', 'no_cases');
    const events = await assets.listEvents('owner');
    expect(events.filter(e => e.event_type === 'optimization')).toHaveLength(1);
    expect(events.find(e => e.event_type === 'optimization')).toMatchObject({ status: 'completed', outcome: 'no_cases' });
  });
});
