// Run after `cargo build -p bcs-cli`: node --test tests/consumer/state_machine_history.cjs
// Exercise the actual Workbench conversion functions without booting React.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const { spawn } = require('node:child_process');
const bcs = path.resolve(__dirname, '../..');
const frontend = path.resolve(bcs, '../frontend');
const ts = require(path.join(frontend, 'node_modules/typescript'));
const cases = JSON.parse(fs.readFileSync(path.join(bcs, 'tests/fixtures/state_machine_history.json')));
const wire = cases.map((entry) => entry.wire);

function loadFunctions(file, names, globals = {}) {
  const source = fs.readFileSync(file, 'utf8');
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
  const selected = ast.statements.filter((node) =>
    ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
  assert.equal(selected.length, names.length, 'every production function must still exist');
  const code = selected.map((node) => node.getText(ast)).join('\n');
  const exports = {};
  const context = vm.createContext({ exports, console: { log() {} }, ...globals });
  vm.runInContext(ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText + '\nObject.assign(exports, {' + names.join(',') + '});', context);
  return exports;
}

test('Workbench preserves each Human, node, attempt and Loop output', () => {
  const types = fs.readFileSync(path.join(frontend, 'src/pages/GroupChat/types.ts'), 'utf8');
  const ast = ts.createSourceFile('types.ts', types, ts.ScriptTarget.Latest, true);
  const declaration = ast.statements.filter(ts.isVariableStatement)
    .flatMap((node) => [...node.declarationList.declarations])
    .find((node) => node.name.getText(ast) === 'BCS_SYSTEM_MESSAGE_BOT_UUID');
  assert.ok(declaration && ts.isStringLiteral(declaration.initializer));
  const globals = { BCS_SYSTEM_MESSAGE_BOT_UUID: declaration.initializer.text };
  const { transformMessageData } = loadFunctions(
    path.join(frontend, 'src/pages/GroupChat/utils/transformMessageData.ts'),
    ['buildBlocksFromMetadata', 'transformMessageData'], globals,
  );
  const { ensureMessageBlocks } = loadFunctions(
    path.join(frontend, 'src/utils/messageUtils.ts'), ['ensureMessageBlocks'],
  );
  const { transformGroupMessagesToChatMessages } = loadFunctions(
    path.join(frontend, 'src/pages/GroupChat/hooks/useGroupChat.ts'),
    ['getStateMachinePayload', 'getStateMachineMetadata', 'isStateMachineTaskMessage',
      'getStateMachineTaskId', 'normalizeStateMachineTaskMessage', 'transformGroupMessagesToChatMessages'],
    { ...globals, ensureMessageBlocks, STATE_MACHINE_SENDER: 'bcs_state_machine', STATE_MACHINE_BOT_NAME: 'BCS State Machine' },
  );
  const converted = transformGroupMessagesToChatMessages(transformMessageData([...wire].reverse()));
  for (const message of wire.filter((message) => message.metadata?.state_machine)) {
    const rendered = converted.find((item) => item.id === message.id);
    assert.ok(rendered, `missing ${message.id}`);
    assert.equal(rendered.role, message.role);
    assert.equal(rendered.content, message.content);
  }
  assert.equal(converted.length, wire.length - 1, 'ordinary chat segments still merge');
  assert.equal(converted.find((m) => m.extra?.conversationRoundId === 'ordinary-round').content, 'ordinary one\n\nordinary two');
  assert.equal(converted.find((m) => m.extra?.conversationRoundId === 'publication-round').content, 'published result');
});

test('existing CLI retains all history messages and metadata', async () => {
  const server = http.createServer((request, response) => {
    assert.equal(request.url, '/sessions/session/messages?limit=20');
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(wire));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const result = await new Promise((resolve, reject) => {
      const child = spawn(path.join(bcs, 'target/debug/bcs-cli'),
        ['--url', `http://127.0.0.1:${server.address().port}`, '--json', 'session', 'messages', 'session', '--limit', '20']);
      let stdout = '', stderr = '';
      child.stdout.on('data', (data) => { stdout += data; });
      child.stderr.on('data', (data) => { stderr += data; });
      child.on('error', reject);
      child.on('exit', (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr)));
    });
    assert.deepEqual(JSON.parse(result), wire);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
