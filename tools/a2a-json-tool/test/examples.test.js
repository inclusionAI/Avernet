import test from 'node:test';
import assert from 'node:assert/strict';
import { demoExamples } from '../web/examples.js';
import { convertInput } from '../web/convert-input.js';

for (const demo of demoExamples.message) {
  test(`消息 Demo 可转换：${demo.title}`, () => {
    const result = convertInput(demo.input, 'message');
    const message = result.output.params.message;
    assert.equal(result.output.method, 'SendMessage');
    assert.equal(message.role, 'ROLE_USER');
    assert.ok(message.messageId);
    if (demo.id === 'message-data') {
      assert.equal(result.mapping, 'data');
      assert.deepEqual(message.parts[0].data, JSON.parse(demo.input));
      assert.equal(Object.hasOwn(message, 'contextId'), false);
    } else {
      assert.equal(result.mapping, 'structured-message');
      assert.equal(message.contextId, 'session_demo');
      assert.ok(message.parts[0].text.length > 0);
      assert.equal(Object.hasOwn(message.parts[0], 'content'), false);
      assert.equal(Object.hasOwn(message, 'taskId'), false);
    }
  });
}

for (const demo of demoExamples.card) {
  test(`AgentCard Demo 可转换：${demo.title}`, () => {
    const { output } = convertInput(demo.input, 'card');
    assert.equal(output.supportedInterfaces[0].protocolVersion, '1.0');
    assert.equal(output.supportedInterfaces[0].protocolBinding, 'JSONRPC');
    assert.equal(Object.hasOwn(output, 'url'), false);
    assert.ok(output.skills[0].tags.length > 0);
    if (demo.id === 'card-complete') assert.deepEqual(output, JSON.parse(demo.input));
  });
}
