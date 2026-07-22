#!/usr/bin/env node
'use strict';

const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const validator = path.join(__dirname, 'validate-state-machine-yaml');
const validText = `name: 通用内容协作
participants:
  planner:
    display_name: 任务规划
    required: true
  researcher:
    display_name: 信息研究
    required: true
  writer:
    display_name: 内容写作
    required: true
runtime:
  kind: state_machine
  state_machine:
    version: 1
    graph_mode: acyclic
    defaults:
      node_timeout_ms: 120000
      max_attempts: 1
    nodes:
      prepare_brief:
        kind: bot_task
        display_name: 准备任务简报
        assignee:
          type: bot_binding
          binding: planner
        instruction: 输出任务目标和约束。
        transitions:
          complete:
            targets:
              - research
              - draft
      research:
        kind: bot_task
        display_name: 研究
        assignee:
          type: bot_binding
          binding: researcher
        instruction: 输出研究结论。
        transitions:
          complete:
            targets:
              - publish
      draft:
        kind: bot_task
        display_name: 起草
        assignee:
          type: bot_binding
          binding: writer
        instruction: 输出内容草稿。
        transitions:
          complete:
            targets:
              - publish
      publish:
        kind: bot_task
        display_name: 汇总交付
        assignee:
          type: bot_binding
          binding: planner
        instruction: 汇总上游产物并输出最终结果。
        final_output: true
`;
const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bcs-custom-collaboration-yaml-'));

function validate(text, env = process.env) {
  const file = path.join(tempDir, 'candidate-' + Math.random().toString(16).slice(2) + '.yaml');
  fs.writeFileSync(file, text);
  const result = spawnSync(validator, [file, '--demo-safe', '--json'], {
    encoding: 'utf8',
    env,
  });
  const wire = result.stdout || result.stderr;
  return { status: result.status, payload: JSON.parse(wire) };
}

function expectInvalid(name, text, code) {
  const result = validate(text);
  assert.strictEqual(result.status, 1, name + ' should fail');
  assert(
    result.payload.errors.some((error) => error.code === code),
    name + ' should include ' + code + ': ' + JSON.stringify(result.payload),
  );
}

try {
  const good = validate(validText);
  assert.strictEqual(good.status, 0, JSON.stringify(good.payload));
  assert.strictEqual(good.payload.valid, true);
  assert.strictEqual(good.payload.summary.participants, 3);
  assert.strictEqual(good.payload.summary.nodes, 4);
  assert.deepStrictEqual(good.payload.summary.initial_nodes, ['prepare_brief']);
  assert.strictEqual(good.payload.summary.final_output_node, 'publish');
  assert(!validText.includes('\n    variables:'));
  assert(!validText.includes('\napi_version:'));
  assert(!validText.includes('\nid:'));
  assert(!validText.includes('\nversion:'));
  assert(!validText.includes('bot_id:'));
  assert(!validText.includes('bcs_participant_role:'));

  const miseResult = spawnSync('mise', ['which', 'openclaw'], { encoding: 'utf8' });
  const commandResult = spawnSync('bash', ['-lc', 'command -v openclaw'], { encoding: 'utf8' });
  const actualOpenClaw = (
    miseResult.status === 0 ? miseResult.stdout : commandResult.stdout
  ).trim();
  assert(actualOpenClaw, 'OpenClaw executable should be discoverable for shim test');

  const shimDir = path.join(tempDir, 'shims');
  fs.mkdirSync(shimDir);
  const openclawShim = path.join(shimDir, 'openclaw');
  const miseShim = path.join(shimDir, 'mise');
  fs.writeFileSync(openclawShim, '#!/usr/bin/env bash\nexit 0\n');
  fs.writeFileSync(
    miseShim,
    '#!/usr/bin/env bash\n' +
      'if [ "$1" = "which" ] && [ "$2" = "openclaw" ]; then\n' +
      '  printf \'%s\\n\' "$TEST_OPENCLAW_REAL"\n' +
      '  exit 0\n' +
      'fi\n' +
      'exit 1\n',
  );
  fs.chmodSync(openclawShim, 0o755);
  fs.chmodSync(miseShim, 0o755);
  const shimmed = validate(validText, {
    ...process.env,
    PATH: shimDir + path.delimiter + process.env.PATH,
    TEST_OPENCLAW_REAL: actualOpenClaw,
  });
  assert.strictEqual(shimmed.status, 0, 'mise shim should resolve: ' + JSON.stringify(shimmed.payload));
  assert.strictEqual(shimmed.payload.valid, true);

  expectInvalid(
    'duplicate key',
    validText.replace('name: 通用内容协作', 'name: 通用内容协作\nname: 重复名称'),
    'DUPLICATE_KEY',
  );
  expectInvalid(
    'top-level api version',
    'api_version: bcs.collaboration/v1\n' + validText,
    'FORBIDDEN_AUTHORING_FIELD',
  );
  expectInvalid(
    'top-level id',
    'id: generated-by-ceo\n' + validText,
    'FORBIDDEN_AUTHORING_FIELD',
  );
  expectInvalid(
    'top-level version',
    'version: 1\n' + validText,
    'FORBIDDEN_AUTHORING_FIELD',
  );
  expectInvalid(
    'misspelled top-level version',
    'verions: 1\n' + validText,
    'UNKNOWN_KEY',
  );
  expectInvalid(
    'embedded bot id',
    validText.replace(
      '    required: true\n  researcher:',
      '    required: true\n    bot_id: real-bot\n  researcher:',
    ),
    'INVALID_PARTICIPANT',
  );
  expectInvalid(
    'missing binding',
    validText.replace('binding: writer', 'binding: missing_writer'),
    'MISSING_BINDING',
  );
  expectInvalid(
    'unknown target',
    validText.replace('- research', '- missing_research'),
    'UNKNOWN_TARGET',
  );
  expectInvalid(
    'multiple finals',
    validText.replace(
      '      research:\n        kind: bot_task',
      '      research:\n        kind: bot_task\n        final_output: true',
    ),
    'FINAL_OUTPUT_COUNT',
  );
  expectInvalid(
    'unsupported variables',
    validText.replace(
      '    defaults:\n',
      '    variables:\n      style: test\n    defaults:\n',
    ),
    'UNSUPPORTED_FEATURE',
  );
  expectInvalid(
    'unsupported judge',
    validText.replace(
      '        final_output: true\n',
      '        judge:\n          type: llm\n        final_output: true\n',
    ),
    'UNSUPPORTED_FEATURE',
  );
  expectInvalid(
    'cycle',
    validText.replace(
      '              - publish\n',
      '              - prepare_brief\n',
    ),
    'CYCLE',
  );
  expectInvalid(
    'invalid timeout',
    validText.replace('node_timeout_ms: 120000', 'node_timeout_ms: 0'),
    'RANGE',
  );
  expectInvalid(
    'metadata labels must be a string map',
    validText.replace(
      'name: 通用内容协作',
      'name: 通用内容协作\nmetadata:\n  labels: not-a-map',
    ),
    'TYPE',
  );
  expectInvalid(
    'participant extensions must be a mapping',
    validText.replace(
      '    required: true\n  researcher:',
      '    required: true\n    extensions: invalid\n  researcher:',
    ),
    'TYPE',
  );
  expectInvalid(
    'projection visibility must be supported',
    validText.replace(
      '    defaults:\n',
      '    projection:\n      default_visibility: public\n    defaults:\n',
    ),
    'ENUM',
  );
  expectInvalid(
    'state machine extensions must be a mapping',
    validText.replace(
      '    defaults:\n',
      '    extensions: invalid\n    defaults:\n',
    ),
    'TYPE',
  );
  expectInvalid(
    'node visibility must be supported',
    validText.replace(
      '        final_output: true\n',
      '        visibility: public\n        final_output: true\n',
    ),
    'ENUM',
  );
  expectInvalid(
    'node extensions must be a mapping',
    validText.replace(
      '        final_output: true\n',
      '        extensions: invalid\n        final_output: true\n',
    ),
    'TYPE',
  );

  process.stdout.write('validator tests passed\n');
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
}
