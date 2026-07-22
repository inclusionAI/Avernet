#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const YAML = require('yaml');

// Keep these checks aligned with the authoring path in:
// - crates/adapters/http/bcs-http/src/routes/groups.rs
//   (reject_authoring_yaml_identity + create-group binding validation)
// - crates/services/bcs-collaboration-runtime/src/definition.rs
//   (validate_definition)
const MAX_DEFINITION_BYTES = 256 * 1024;

function finish(payload, code, jsonOutput) {
  if (jsonOutput) {
    process.stdout.write(JSON.stringify(payload) + '\n');
  } else if (payload.valid) {
    process.stdout.write('VALID\n');
  } else {
    for (const error of payload.errors) {
      process.stdout.write(error.code + ' ' + error.path + ': ' + error.message + '\n');
    }
  }
  process.exit(code);
}

function usage(message) {
  process.stderr.write(JSON.stringify({
    valid: false,
    errors: [{ code: 'USAGE', path: '$', message }],
  }) + '\n');
  process.exit(2);
}

const args = process.argv.slice(2);
let inputPath = null;
let jsonOutput = false;
let demoSafe = false;
for (const arg of args) {
  if (arg === '--json') jsonOutput = true;
  else if (arg === '--demo-safe') demoSafe = true;
  else if (arg.startsWith('-')) usage('unknown option: ' + arg);
  else if (inputPath === null) inputPath = arg;
  else usage('only one YAML file may be provided');
}
if (!inputPath) usage('usage: validate-state-machine-yaml <file> [--demo-safe] [--json]');

let raw;
try {
  raw = fs.readFileSync(inputPath, 'utf8');
} catch (error) {
  process.stderr.write(JSON.stringify({
    valid: false,
    errors: [{ code: 'FILE_READ', path: '$', message: error.message }],
  }) + '\n');
  process.exit(2);
}

const errors = [];
const warnings = [];
const add = (code, path, message, hint) => {
  errors.push({ code, path, message, ...(hint ? { hint } : {}) });
};
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const nonEmptyString = (value) => typeof value === 'string' && value.trim().length > 0;
const positiveInteger = (value) => Number.isInteger(value) && value > 0;
const requireObject = (value, path) => {
  if (!isObject(value)) {
    add('TYPE', path, 'must be a mapping');
    return false;
  }
  return true;
};
const allowKeys = (value, allowed, path) => {
  if (!isObject(value)) return;
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) add('UNKNOWN_KEY', path + '.' + key, 'unsupported or misspelled field');
  }
};
const requireStringMap = (value, path) => {
  if (!requireObject(value, path)) return false;
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'string') {
      add('TYPE', path + '.' + key, 'must be a string');
    }
  }
  return true;
};
const requireEnum = (value, allowed, path) => {
  if (!allowed.has(value)) {
    add('ENUM', path, 'must be one of: ' + [...allowed].join(', '));
    return false;
  }
  return true;
};
const projectionVisibilities = new Set(['private', 'shared']);

if (Buffer.byteLength(raw, 'utf8') > MAX_DEFINITION_BYTES) {
  add(
    'SIZE_LIMIT',
    '$',
    'collaboration_definition_yaml exceeds ' + MAX_DEFINITION_BYTES + ' bytes',
  );
}

let documents = [];
try {
  documents = YAML.parseAllDocuments(raw, {
    prettyErrors: true,
    strict: true,
    uniqueKeys: true,
  });
} catch (error) {
  add('YAML_PARSE', '$', error.message);
}
for (const document of documents) {
  for (const error of document.errors || []) {
    add(/unique|duplicate/i.test(error.message) ? 'DUPLICATE_KEY' : 'YAML_PARSE', '$', error.message);
  }
}
if (documents.length !== 1) add('YAML_DOCUMENT_COUNT', '$', 'exactly one YAML document is required');

let definition = null;
if (errors.length === 0) {
  try {
    definition = documents[0].toJS({ maxAliasCount: 0 });
  } catch (error) {
    add('YAML_PARSE', '$', error.message);
  }
}

const summary = {
  participants: 0,
  nodes: 0,
  initial_nodes: [],
  final_output_node: null,
};

if (definition !== null && requireObject(definition, '$')) {
  const forbiddenTopLevelFields = new Set(['api_version', 'id', 'version']);
  for (const field of forbiddenTopLevelFields) {
    if (Object.prototype.hasOwnProperty.call(definition, field)) {
      add(
        'FORBIDDEN_AUTHORING_FIELD',
        '$.' + field,
        'must be omitted from create-group authoring YAML; BCS supplies this value',
      );
    }
  }
  // Include explicitly forbidden fields here so they produce one precise error
  // instead of an additional generic UNKNOWN_KEY error.
  allowKeys(definition, new Set([
    'api_version', 'id', 'version', 'name', 'metadata', 'participants', 'runtime',
  ]), '$');
  if (!nonEmptyString(definition.name)) add('REQUIRED', '$.name', 'must be a non-empty string');

  if (definition.metadata !== undefined &&
      requireObject(definition.metadata, '$.metadata')) {
    allowKeys(definition.metadata, new Set(['description', 'labels', 'extensions']), '$.metadata');
    if (definition.metadata.description !== undefined &&
        definition.metadata.description !== null &&
        typeof definition.metadata.description !== 'string') {
      add('TYPE', '$.metadata.description', 'must be a string or null');
    }
    if (definition.metadata.labels !== undefined) {
      requireStringMap(definition.metadata.labels, '$.metadata.labels');
    }
    if (definition.metadata.extensions !== undefined) {
      requireObject(definition.metadata.extensions, '$.metadata.extensions');
    }
  }
  const participantIds = new Set();
  const requiredParticipants = new Set();
  const participants = definition.participants;
  if (requireObject(participants, '$.participants')) {
    if (Object.keys(participants).length === 0) {
      add('REQUIRED', '$.participants', 'must not be empty');
    }
    for (const [binding, participant] of Object.entries(participants)) {
      const participantPath = '$.participants.' + binding;
      participantIds.add(binding);
      if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(binding)) {
        add('FORMAT', participantPath, 'binding id has an invalid format');
      }
      if (!requireObject(participant, participantPath)) continue;
      if ('bot_id' in participant || 'bcs_participant_role' in participant) {
        add('INVALID_PARTICIPANT', participantPath, 'runtime bot ids and participant roles must not be embedded in YAML');
      }
      allowKeys(participant, new Set([
        'display_name', 'description', 'required', 'extensions',
      ]), participantPath);
      if (participant.display_name !== undefined &&
          !nonEmptyString(participant.display_name)) {
        add('TYPE', participantPath + '.display_name', 'must be a non-empty string');
      }
      if (participant.description !== undefined &&
          !nonEmptyString(participant.description)) {
        add('TYPE', participantPath + '.description', 'must be a non-empty string');
      }
      if (participant.required !== undefined &&
          typeof participant.required !== 'boolean') {
        add('TYPE', participantPath + '.required', 'must be boolean');
      }
      if (participant.extensions !== undefined) {
        requireObject(participant.extensions, participantPath + '.extensions');
      }
      if (participant.required === true) requiredParticipants.add(binding);
    }
    summary.participants = Object.keys(participants).length;
  }

  const runtime = definition.runtime;
  let nodes = null;
  if (requireObject(runtime, '$.runtime')) {
    allowKeys(runtime, new Set(['kind', 'state_machine']), '$.runtime');
    if (runtime.kind !== 'state_machine') {
      add('RUNTIME_KIND', '$.runtime.kind', 'must equal state_machine');
    }
    const machine = runtime.state_machine;
    if (requireObject(machine, '$.runtime.state_machine')) {
      allowKeys(machine, new Set([
        'version', 'graph_mode', 'projection', 'defaults', 'nodes',
        'extensions', 'initial_node', 'input_schema', 'variables', 'events',
      ]), '$.runtime.state_machine');
      if (machine.version !== 1) {
        add('VERSION', '$.runtime.state_machine.version', 'must equal 1');
      }
      if (machine.graph_mode !== 'acyclic') {
        add('UNSUPPORTED_FEATURE', '$.runtime.state_machine.graph_mode', 'demo workflows require acyclic');
      }
      if (machine.initial_node !== undefined) {
        add('UNSUPPORTED_FEATURE', '$.runtime.state_machine.initial_node', 'initial_node is not supported in MVP');
      }
      if (machine.variables !== undefined &&
          (!isObject(machine.variables) || Object.keys(machine.variables).length > 0)) {
        add('UNSUPPORTED_FEATURE', '$.runtime.state_machine.variables', 'variables are not supported in MVP');
      }
      if (machine.events !== undefined &&
          (!isObject(machine.events) || Object.keys(machine.events).length > 0)) {
        add('UNSUPPORTED_FEATURE', '$.runtime.state_machine.events', 'events are not supported in MVP');
      }
      if (demoSafe && machine.input_schema !== undefined) {
        add('UNSUPPORTED_FEATURE', '$.runtime.state_machine.input_schema', 'input_schema is excluded from demo-safe workflows');
      }
      if (machine.projection !== undefined &&
          requireObject(machine.projection, '$.runtime.state_machine.projection')) {
        allowKeys(machine.projection, new Set(['default_visibility']), '$.runtime.state_machine.projection');
        if (machine.projection.default_visibility !== undefined) {
          requireEnum(
            machine.projection.default_visibility,
            projectionVisibilities,
            '$.runtime.state_machine.projection.default_visibility',
          );
        }
      }
      if (machine.defaults !== undefined &&
          requireObject(machine.defaults, '$.runtime.state_machine.defaults')) {
        allowKeys(machine.defaults, new Set([
          'node_timeout_ms', 'max_attempts',
        ]), '$.runtime.state_machine.defaults');
        if (machine.defaults.node_timeout_ms !== undefined &&
            !positiveInteger(machine.defaults.node_timeout_ms)) {
          add('RANGE', '$.runtime.state_machine.defaults.node_timeout_ms', 'must be a positive integer');
        }
        if (machine.defaults.max_attempts !== undefined &&
            !positiveInteger(machine.defaults.max_attempts)) {
          add('RANGE', '$.runtime.state_machine.defaults.max_attempts', 'must be a positive integer');
        }
      }
      if (machine.extensions !== undefined) {
        requireObject(machine.extensions, '$.runtime.state_machine.extensions');
      }
      nodes = machine.nodes;
    }
  }

  if (requireObject(nodes, '$.runtime.state_machine.nodes')) {
    const nodeIds = Object.keys(nodes);
    summary.nodes = nodeIds.length;
    if (nodeIds.length === 0) {
      add('REQUIRED', '$.runtime.state_machine.nodes', 'must not be empty');
    }
    const adjacency = new Map(nodeIds.map((id) => [id, []]));
    const reverse = new Map(nodeIds.map((id) => [id, []]));
    const indegree = new Map(nodeIds.map((id) => [id, 0]));
    const usedBindings = new Set();
    const finals = [];

    for (const [nodeId, node] of Object.entries(nodes)) {
      const nodePath = '$.runtime.state_machine.nodes.' + nodeId;
      if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(nodeId)) {
        add('FORMAT', nodePath, 'node id has an invalid format');
      }
      if (!requireObject(node, nodePath)) continue;
      allowKeys(node, new Set([
        'kind', 'display_name', 'assignee', 'instruction',
        'node_timeout_ms', 'max_attempts', 'transitions', 'visibility',
        'final_output', 'extensions', 'judge', 'output_contract', 'action',
      ]), nodePath);
      if (node.kind !== 'bot_task') {
        add('UNSUPPORTED_FEATURE', nodePath + '.kind', 'only bot_task is supported in MVP');
      }
      if (!nonEmptyString(node.display_name)) {
        add('REQUIRED', nodePath + '.display_name', 'must be a non-empty string');
      }
      if (!nonEmptyString(node.instruction)) {
        add('REQUIRED', nodePath + '.instruction', 'must be a non-empty string');
      }
      if (node.node_timeout_ms !== undefined &&
          !positiveInteger(node.node_timeout_ms)) {
        add('RANGE', nodePath + '.node_timeout_ms', 'must be a positive integer');
      }
      if (node.max_attempts !== undefined &&
          !positiveInteger(node.max_attempts)) {
        add('RANGE', nodePath + '.max_attempts', 'must be a positive integer');
      }
      if (node.visibility !== undefined && node.visibility !== null) {
        requireEnum(node.visibility, projectionVisibilities, nodePath + '.visibility');
      }
      if (node.extensions !== undefined) {
        requireObject(node.extensions, nodePath + '.extensions');
      }
      for (const field of ['judge', 'output_contract', 'action']) {
        if (node[field] !== undefined) {
          add('UNSUPPORTED_FEATURE', nodePath + '.' + field, field + ' is excluded from demo-safe workflows');
        }
      }

      if (requireObject(node.assignee, nodePath + '.assignee')) {
        allowKeys(node.assignee, new Set(['type', 'binding']), nodePath + '.assignee');
        if (node.assignee.type !== 'bot_binding') {
          add('UNSUPPORTED_FEATURE', nodePath + '.assignee.type', 'must equal bot_binding');
        }
        if (!nonEmptyString(node.assignee.binding)) {
          add('REQUIRED', nodePath + '.assignee.binding', 'must be a non-empty string');
        } else if (!participantIds.has(node.assignee.binding)) {
          add('MISSING_BINDING', nodePath + '.assignee.binding', 'participant binding not found: ' + node.assignee.binding);
        } else {
          usedBindings.add(node.assignee.binding);
        }
      }

      if (node.final_output === true) finals.push(nodeId);
      else if (node.final_output !== undefined &&
               typeof node.final_output !== 'boolean') {
        add('TYPE', nodePath + '.final_output', 'must be boolean');
      }

      const transitions = node.transitions;
      if (node.final_output === true) {
        if (transitions !== undefined &&
            (!isObject(transitions) || Object.keys(transitions).length > 0)) {
          add('FINAL_HAS_TRANSITION', nodePath + '.transitions', 'final output node must be a sink');
        }
      } else if (!requireObject(transitions, nodePath + '.transitions')) {
        add('MISSING_TRANSITION', nodePath + '.transitions', 'non-final node requires transitions.complete.targets');
      }
      if (isObject(transitions)) {
        for (const outcome of Object.keys(transitions)) {
          if (outcome !== 'complete') {
            add('UNSUPPORTED_FEATURE', nodePath + '.transitions.' + outcome, 'only complete transitions are supported');
          }
        }
        const complete = transitions.complete;
        if (node.final_output !== true && !isObject(complete)) {
          add('MISSING_TRANSITION', nodePath + '.transitions.complete', 'non-final node requires a complete transition');
        }
        if (isObject(complete)) {
          allowKeys(complete, new Set(['targets', 'guard']), nodePath + '.transitions.complete');
          if (complete.guard !== undefined) {
            add('UNSUPPORTED_FEATURE', nodePath + '.transitions.complete.guard', 'guarded transitions are not supported in MVP');
          }
          if (!Array.isArray(complete.targets) ||
              complete.targets.length === 0 ||
              complete.targets.some((target) => typeof target !== 'string')) {
            add('TYPE', nodePath + '.transitions.complete.targets', 'must be a non-empty string list');
          } else {
            for (const target of complete.targets) {
              if (!adjacency.has(target)) {
                add('UNKNOWN_TARGET', nodePath + '.transitions.complete.targets', 'target node not found: ' + target);
              } else {
                adjacency.get(nodeId).push(target);
                reverse.get(target).push(nodeId);
                indegree.set(target, indegree.get(target) + 1);
              }
            }
          }
        }
      }
    }

    if (demoSafe ? finals.length !== 1 : finals.length > 1) {
      add(
        'FINAL_OUTPUT_COUNT',
        '$.runtime.state_machine.nodes',
        demoSafe
          ? 'demo-safe workflow requires exactly one final_output node'
          : 'workflow may have at most one final_output node',
      );
    }
    summary.final_output_node = finals.length === 1 ? finals[0] : null;
    for (const binding of requiredParticipants) {
      if (!usedBindings.has(binding)) {
        add('UNUSED_PARTICIPANT', '$.participants.' + binding, 'required participant is never assigned a node');
      }
    }

    const initialNodes = [...indegree.entries()]
      .filter(([, degree]) => degree === 0)
      .map(([id]) => id);
    summary.initial_nodes = initialNodes;
    if (initialNodes.length === 0) {
      add('CYCLE', '$.runtime.state_machine.nodes', 'graph has no zero in-degree node');
    }
    if (demoSafe && initialNodes.length !== 1) {
      add('INITIAL_NODE_COUNT', '$.runtime.state_machine.nodes', 'demo-safe workflow requires exactly one zero in-degree entry node');
    }

    const queue = [...initialNodes];
    const remaining = new Map(indegree);
    const topo = [];
    while (queue.length > 0) {
      const id = queue.shift();
      topo.push(id);
      for (const target of adjacency.get(id) || []) {
        remaining.set(target, remaining.get(target) - 1);
        if (remaining.get(target) === 0) queue.push(target);
      }
    }
    if (topo.length !== nodeIds.length) {
      add('CYCLE', '$.runtime.state_machine.nodes', 'complete-transition graph must be acyclic');
    }

    if (initialNodes.length === 1) {
      const reachable = new Set();
      const stack = [initialNodes[0]];
      while (stack.length > 0) {
        const id = stack.pop();
        if (reachable.has(id)) continue;
        reachable.add(id);
        stack.push(...(adjacency.get(id) || []));
      }
      for (const id of nodeIds) {
        if (!reachable.has(id)) {
          add('UNREACHABLE_NODE', '$.runtime.state_machine.nodes.' + id, 'node is not reachable from the entry');
        }
      }
    }

    if (finals.length === 1) {
      const canReachFinal = new Set();
      const stack = [finals[0]];
      while (stack.length > 0) {
        const id = stack.pop();
        if (canReachFinal.has(id)) continue;
        canReachFinal.add(id);
        stack.push(...(reverse.get(id) || []));
      }
      for (const id of nodeIds) {
        if (!canReachFinal.has(id)) {
          add('NO_FINAL_PATH', '$.runtime.state_machine.nodes.' + id, 'node cannot reach the final output');
        }
      }
    }
  }
}

finish({ valid: errors.length === 0, errors, warnings, summary }, errors.length === 0 ? 0 : 1, jsonOutput);
