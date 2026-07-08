const $ = (id) => document.getElementById(id);

const state = {
  ws: null,
  reqId: 0,
  currentRunId: null,
  assistantByRunId: new Map(),
  thinkingByRunId: new Map(),
  pendingInteractions: new Map(), // interactionId -> { runId, kind, ... }
  currentModelByRunId: new Map(),
  currentSessionIdByRunId: new Map(),
  toolUpdateByRunId: new Map(), // runId -> Map<toolCallId, string>
  currentCwd: '', // workspace directory from server
};

function nextId() {
  state.reqId += 1;
  return String(state.reqId);
}

function setStatus(kind, text) {
  $('statusDot').className = `dot ${kind || ''}`.trim();
  $('statusText').textContent = text;
}

function setRunStatus(runState) {
  const el = $('runStatus');
  if (!el) return;
  if (!runState) {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'inline-flex';
  el.innerHTML = '';
  const dot = document.createElement('span');
  dot.className = 'dot';
  const label = document.createElement('span');
  if (runState === 'running') {
    dot.className = 'dot ok';
    label.textContent = '回答中';
    el.style.borderColor = 'var(--accent-2)';
  } else if (runState === 'waiting_human') {
    dot.className = 'dot';
    dot.style.background = 'var(--warn)';
    label.textContent = '等待审批';
    el.style.borderColor = 'var(--warn)';
  } else if (runState === 'completed') {
    dot.className = 'dot ok';
    label.textContent = '回答完成';
    el.style.borderColor = 'var(--accent-2)';
  } else if (runState === 'error') {
    dot.className = 'dot err';
    label.textContent = '出错';
    el.style.borderColor = 'var(--danger)';
  } else if (runState === 'aborted') {
    dot.className = 'dot err';
    label.textContent = '已中止';
    el.style.borderColor = 'var(--danger)';
  } else {
    dot.className = 'dot';
    label.textContent = runState;
  }
  el.appendChild(dot);
  el.appendChild(label);
}

function scrollChat() {
  const el = $('chat');
  el.scrollTop = el.scrollHeight;
}

function syncCwd(cwd) {
  if (cwd && typeof cwd === 'string') {
    state.currentCwd = cwd;
    const input = $('cwd');
    // Only overwrite the input if the user hasn't typed a different value
    if (document.activeElement !== input) {
      input.value = cwd;
    }
    const status = $('cwdStatus');
    if (status) status.textContent = `Active: ${cwd}`;
  }
}

function addEvent(frame) {
  const el = document.createElement('div');
  el.className = 'evt';
  el.innerHTML = `<div class="meta">${new Date().toLocaleTimeString()}</div><pre></pre>`;
  el.querySelector('pre').textContent = JSON.stringify(frame, null, 2);
  $('events').prepend(el);
}

function addChat(role, text, runId, extraClass = '') {
  const el = document.createElement('div');
  el.className = `msg ${role} ${extraClass}`.trim();
  el.dataset.runId = runId || '';
  el.dataset.msgType = extraClass || role;
  el.innerHTML = `<div class="meta">${role}${runId ? ` · ${runId.slice(0, 8)}` : ''}</div><pre></pre>`;
  el.querySelector('pre').textContent = text;
  applyChatFilter(el);
  // thinking: default collapsed, click meta to toggle
  if (extraClass === 'thinking') {
    el.classList.add('collapsed');
    const meta = el.querySelector('.meta');
    const icon = document.createElement('span');
    icon.className = 'toggle-icon';
    icon.textContent = '▼';
    meta.appendChild(icon);
    const hint = document.createElement('span');
    hint.className = 'collapse-hint';
    hint.textContent = '(click to expand)';
    meta.appendChild(hint);
    meta.addEventListener('click', () => {
      el.classList.toggle('collapsed');
      const len = el.querySelector('pre').textContent.length;
      hint.textContent = el.classList.contains('collapsed')
        ? `(${len} chars, click to expand)`
        : '(click to collapse)';
    });
  }
  $('chat').append(el);
  scrollChat();
  return el;
}

function applyChatFilter(el) {
  const type = el.dataset.msgType;
  if (type === 'thinking') {
    el.style.display = $('showThinking').checked ? '' : 'none';
  } else if (type === 'tool_use') {
    el.style.display = $('showTool').checked ? '' : 'none';
  } else if (type === 'system_event') {
    el.style.display = $('showSystem').checked ? '' : 'none';
  }
}

function refreshChatFilter() {
  $('chat').querySelectorAll('.msg').forEach(applyChatFilter);
}

function upsertAssistant(runId, text, final = false, extra = '') {
  let el = state.assistantByRunId.get(runId);
  if (!el) {
    el = addChat('assistant', text, runId);
    state.assistantByRunId.set(runId, el);
  } else {
    el.querySelector('pre').textContent = text;
  }
  const meta = el.querySelector('.meta');
  const model = state.currentModelByRunId.get(runId);
  const modelTag = model ? ` · ${model}` : '';
  meta.textContent = `assistant${runId ? ` · ${runId.slice(0, 8)}` : ''}${modelTag}${final ? ' · final' : ' · streaming'}${extra ? ` · ${extra}` : ''}`;
  scrollChat();
}

function send(method, params) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    alert('WebSocket not connected');
    return;
  }
  const frame = { type: 'req', id: nextId(), method, params };
  state.ws.send(JSON.stringify(frame));
  addEvent({ direction: 'out', ...frame });
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---- Unified Interaction Renderers ----

function getInteractionIcon(kind) {
  switch (kind) {
    case 'ask_user': return '🤔';
    case 'confirm': return '⚠️';
    case 'mode_switch': return '🔀';
    default: return '❓';
  }
}

function getInteractionTitle(kind) {
  switch (kind) {
    case 'ask_user': return 'Awaiting Human Input';
    case 'confirm': return 'Action Approval Required';
    case 'mode_switch': return 'Mode Transition Request';
    default: return 'Interaction Required';
  }
}

function getInteractionBadgeClass(kind) {
  switch (kind) {
    case 'ask_user': return 'legend-interaction';
    case 'confirm': return 'legend-hitl';
    case 'mode_switch': return 'legend-mode';
    default: return 'legend-interaction';
  }
}

// ---- AskUserQuestion Renderer ----

function renderAskUserBanner(interactionId, runId, sessionKey, data) {
  const banner = $('hitlBanner');
  banner.style.display = 'block';
  banner.innerHTML = '';
  banner.className = 'hitl-banner';
  banner.dataset.interactionId = interactionId;

  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = `${getInteractionIcon(data.kind)} ${data.title || getInteractionTitle(data.kind)}`;
  banner.appendChild(title);

  if (data.description) {
    const desc = document.createElement('div');
    desc.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;';
    desc.textContent = data.description;
    banner.appendChild(desc);
  }

  const answersMap = {}; // idx -> string (single select / text) or string[] (multiSelect)
  const inputRefs = {};

  (data.questions || []).forEach((q, idx) => {
    const qDiv = document.createElement('div');
    qDiv.className = 'hitl-question';

    if (q.header) {
      const header = document.createElement('div');
      header.className = 'q-header';
      header.textContent = q.header;
      qDiv.appendChild(header);
    }

    const qText = document.createElement('div');
    qText.className = 'q-text';
    if (q.multiSelect) {
      qText.textContent = q.question + ' (可多选)';
    } else {
      qText.textContent = q.question;
    }
    qDiv.appendChild(qText);

    if (q.options?.length) {
      const optRow = document.createElement('div');
      optRow.className = 'hitl-options';
      if (q.multiSelect) {
        answersMap[idx] = [];
        q.options.forEach(opt => {
          const btn = document.createElement('button');
          btn.className = 'secondary';
          btn.textContent = opt.label;
          btn.title = opt.description || '';
          btn.onclick = (e) => {
            e.preventDefault();
            const isSelected = btn.classList.toggle('selected');
            if (isSelected) {
              answersMap[idx].push(opt.label);
            } else {
              answersMap[idx] = answersMap[idx].filter(l => l !== opt.label);
            }
            if (inputRefs[idx]) {
              inputRefs[idx].value = answersMap[idx].join(', ');
              inputRefs[idx]._fromButton = true;
            }
          };
          optRow.appendChild(btn);
        });
      } else {
        q.options.forEach(opt => {
          const btn = document.createElement('button');
          btn.className = 'secondary';
          btn.textContent = opt.label;
          btn.title = opt.description || '';
          btn.onclick = () => {
            optRow.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            answersMap[idx] = opt.label;
            if (inputRefs[idx]) inputRefs[idx].value = opt.label;
          };
          optRow.appendChild(btn);
        });
      }
      qDiv.appendChild(optRow);
    }

    const inputWrap = document.createElement('div');
    inputWrap.className = 'hitl-input';
    const input = document.createElement('input');
    input.placeholder = q.options?.length
      ? (q.multiSelect ? 'Or type custom answer (or leave empty)...' : 'Or type custom answer...')
      : 'Type your answer...';
    input.oninput = () => {
      if (input._fromButton) {
        input._fromButton = false;
        return;
      }
      answersMap[idx] = input.value;
      if (q.options?.length) {
        const optRow = qDiv.querySelector('.hitl-options');
        if (optRow) optRow.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
        if (q.multiSelect) answersMap[idx] = input.value ? [input.value] : [];
      }
    };
    inputWrap.appendChild(input);
    qDiv.appendChild(inputWrap);
    inputRefs[idx] = input;

    banner.appendChild(qDiv);
  });

  const btnRow = document.createElement('div');
  btnRow.className = 'btns';
  const submitBtn = document.createElement('button');
  submitBtn.className = 'hitl';
  submitBtn.textContent = 'Submit';
  submitBtn.onclick = () => {
    const answers = [];
    const selectedOptions = [];
    data.questions?.forEach((q, idx) => {
      const val = answersMap[idx];
      if (Array.isArray(val)) {
        if (val.length > 0) {
          answers.push(val.join('; '));
          selectedOptions.push(...val);
        }
      } else if (val) {
        answers.push(val);
        selectedOptions.push(val);
      } else if (inputRefs[idx]?.value) {
        answers.push(inputRefs[idx].value);
        selectedOptions.push(inputRefs[idx].value);
      }
    });
    const answer = answers.filter(Boolean).join('\n') || '(responded)';
    send('interaction.resolve', {
      interactionId,
      decision: 'submit',
      answer,
      selectedOptions: selectedOptions.length > 0 ? selectedOptions : undefined,
    });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(submitBtn);
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'ghost';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'cancel' });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(cancelBtn);
  banner.appendChild(btnRow);
}

function renderAskUserInline(interactionId, runId, sessionKey, data) {
  const el = document.createElement('div');
  el.className = 'msg human_input';
  el.dataset.runId = runId || '';
  el.dataset.msgType = 'human_input';
  el.dataset.interactionId = interactionId;

  const meta = document.createElement('div');
  meta.className = 'meta';
  const badge = document.createElement('span');
  badge.className = 'approval-badge waiting';
  badge.textContent = 'waiting';
  meta.innerHTML = `<span style="margin-right:4px;">${getInteractionIcon(data.kind)}</span> ${data.kind}${runId ? ` · ${runId.slice(0, 8)}` : ''} `;
  meta.appendChild(badge);
  el.appendChild(meta);

  if (data.title) {
    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-weight:700;margin-bottom:8px;color:var(--accent-2);';
    titleEl.textContent = data.title;
    el.appendChild(titleEl);
  }

  if (data.description) {
    const descEl = document.createElement('div');
    descEl.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;';
    descEl.textContent = data.description;
    el.appendChild(descEl);
  }

  const answersMap = {};
  const inputRefs = {};

  (data.questions || []).forEach((q, idx) => {
    const block = document.createElement('div');
    block.className = 'ask-block';

    if (q.header) {
      const header = document.createElement('div');
      header.className = 'q-header';
      header.textContent = q.header;
      block.appendChild(header);
    }

    const qText = document.createElement('div');
    qText.className = 'q-text';
    if (q.multiSelect) {
      qText.textContent = q.question + ' (可多选)';
    } else {
      qText.textContent = q.question;
    }
    block.appendChild(qText);

    if (q.options?.length) {
      const optRow = document.createElement('div');
      optRow.className = 'ask-options';
      if (q.multiSelect) {
        answersMap[idx] = [];
        q.options.forEach(opt => {
          const btn = document.createElement('button');
          btn.textContent = opt.label;
          btn.title = opt.description || '';
          if (opt.preview) btn.title += `\nPreview: ${opt.preview}`;
          btn.onclick = (e) => {
            e.preventDefault();
            const isSelected = btn.classList.toggle('selected');
            if (isSelected) {
              answersMap[idx].push(opt.label);
            } else {
              answersMap[idx] = answersMap[idx].filter(l => l !== opt.label);
            }
            if (inputRefs[idx]) {
              inputRefs[idx].value = answersMap[idx].join(', ');
              inputRefs[idx]._fromButton = true;
            }
          };
          optRow.appendChild(btn);
        });
      } else {
        q.options.forEach(opt => {
          const btn = document.createElement('button');
          btn.textContent = opt.label;
          btn.title = opt.description || '';
          if (opt.preview) btn.title += `\nPreview: ${opt.preview}`;
          btn.onclick = () => {
            optRow.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            answersMap[idx] = opt.label;
            if (inputRefs[idx]) inputRefs[idx].value = opt.label;
          };
          optRow.appendChild(btn);
        });
      }
      block.appendChild(optRow);
    }

    const inputWrap = document.createElement('div');
    inputWrap.className = 'hitl-input';
    const input = document.createElement('input');
    input.placeholder = q.options?.length
      ? (q.multiSelect ? 'Or type custom answer (or leave empty)...' : 'Or type custom answer...')
      : 'Type your answer...';
    input.oninput = () => {
      if (input._fromButton) {
        input._fromButton = false;
        return;
      }
      answersMap[idx] = input.value;
      if (q.options?.length) {
        const optRow = block.querySelector('.ask-options');
        if (optRow) optRow.querySelectorAll('button').forEach(b => b.classList.remove('selected'));
        if (q.multiSelect) answersMap[idx] = input.value ? [input.value] : [];
      }
    };
    inputWrap.appendChild(input);
    block.appendChild(inputWrap);
    inputRefs[idx] = input;

    el.appendChild(block);
  });

  const actions = document.createElement('div');
  actions.className = 'ask-actions';
  const submitBtn = document.createElement('button');
  submitBtn.className = 'hitl';
  submitBtn.textContent = 'Submit';
  submitBtn.onclick = () => {
    const answers = [];
    const selectedOptions = [];
    data.questions?.forEach((q, idx) => {
      const val = answersMap[idx];
      if (Array.isArray(val)) {
        if (val.length > 0) {
          answers.push(val.join('; '));
          selectedOptions.push(...val);
        }
      } else if (val) {
        answers.push(val);
        selectedOptions.push(val);
      } else if (inputRefs[idx]?.value) {
        answers.push(inputRefs[idx].value);
        selectedOptions.push(inputRefs[idx].value);
      }
    });
    const answer = answers.filter(Boolean).join('\n') || '(responded)';
    send('interaction.resolve', {
      interactionId,
      decision: 'submit',
      answer,
      selectedOptions: selectedOptions.length > 0 ? selectedOptions : undefined,
    });
    badge.className = 'approval-badge responding';
    badge.textContent = 'responding';
    actions.querySelectorAll('button').forEach(b => b.disabled = true);
    addChat('user', `→ ${answer}`, runId, 'human_resolved');
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(submitBtn);
  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'ghost';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'cancel' });
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(cancelBtn);
  el.appendChild(actions);

  applyChatFilter(el);
  $('chat').append(el);
  scrollChat();
}

// ---- Confirm Renderer (Bash/Edit/Write/Read) ----

function renderConfirmBanner(interactionId, runId, sessionKey, data) {
  const banner = $('hitlBanner');
  banner.style.display = 'block';
  banner.innerHTML = '';
  banner.className = 'hitl-banner';
  banner.dataset.interactionId = interactionId;

  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = `${getInteractionIcon(data.kind)} ${data.title || getInteractionTitle(data.kind)}`;
  banner.appendChild(title);

  if (data.description) {
    const desc = document.createElement('div');
    desc.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;';
    desc.textContent = data.description;
    banner.appendChild(desc);
  }

  // Subject details
  const subject = data.subject || {};
  const infoDiv = document.createElement('div');
  infoDiv.className = 'hitl-question';

  if (subject.type === 'command') {
    infoDiv.innerHTML = `
      <div style="margin-bottom:8px;"><strong>Command:</strong></div>
      <code style="background:var(--panel-2);padding:8px 12px;border-radius:6px;display:block;margin-bottom:8px;">${escapeHtml(subject.command || 'unknown')}</code>
      ${subject.cwd ? `<div style="font-size:11px;color:var(--muted);">cwd: ${escapeHtml(subject.cwd)}</div>` : ''}
    `;
  } else if (subject.type === 'file') {
    infoDiv.innerHTML = `
      <div style="margin-bottom:8px;"><strong>File:</strong></div>
      <code style="background:var(--panel-2);padding:8px 12px;border-radius:6px;display:block;margin-bottom:8px;">${escapeHtml(subject.filePath || 'unknown')}</code>
      <div style="font-size:11px;color:var(--muted);">Action: ${escapeHtml(subject.toolName || 'modify')}</div>
    `;
  } else {
    infoDiv.innerHTML = `
      <div style="margin-bottom:8px;"><strong>Tool:</strong> ${escapeHtml(subject.toolName || 'unknown')}</div>
    `;
  }
  banner.appendChild(infoDiv);

  const btnRow = document.createElement('div');
  btnRow.className = 'btns';

  const approveBtn = document.createElement('button');
  approveBtn.className = 'hitl';
  approveBtn.textContent = '✓ Approve';
  approveBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'approve' });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(approveBtn);

  const denyBtn = document.createElement('button');
  denyBtn.className = 'danger';
  denyBtn.textContent = '✗ Deny';
  denyBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'deny' });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(denyBtn);

  banner.appendChild(btnRow);
}

function renderConfirmInline(interactionId, runId, sessionKey, data) {
  const el = document.createElement('div');
  el.className = 'msg human_input';
  el.dataset.runId = runId || '';
  el.dataset.msgType = 'human_input';
  el.dataset.interactionId = interactionId;

  const meta = document.createElement('div');
  meta.className = 'meta';
  const badge = document.createElement('span');
  badge.className = 'approval-badge waiting';
  badge.textContent = 'waiting';
  meta.innerHTML = `<span style="margin-right:4px;">${getInteractionIcon(data.kind)}</span> ${data.kind}${runId ? ` · ${runId.slice(0, 8)}` : ''} `;
  meta.appendChild(badge);
  el.appendChild(meta);

  if (data.title) {
    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-weight:700;margin-bottom:8px;color:var(--accent-2);';
    titleEl.textContent = data.title;
    el.appendChild(titleEl);
  }

  const subject = data.subject || {};
  const block = document.createElement('div');
  block.className = 'ask-block';

  if (subject.type === 'command') {
    block.innerHTML = `
      <div style="margin-bottom:8px;"><strong>Command:</strong></div>
      <code style="background:var(--panel-2);padding:8px 12px;border-radius:6px;display:block;margin-bottom:8px;">${escapeHtml(subject.command || 'unknown')}</code>
      ${subject.cwd ? `<div style="font-size:11px;color:var(--muted);">cwd: ${escapeHtml(subject.cwd)}</div>` : ''}
    `;
  } else if (subject.type === 'file') {
    block.innerHTML = `
      <div style="margin-bottom:8px;"><strong>File:</strong></div>
      <code style="background:var(--panel-2);padding:8px 12px;border-radius:6px;display:block;margin-bottom:8px;">${escapeHtml(subject.filePath || 'unknown')}</code>
      <div style="font-size:11px;color:var(--muted);">Action: ${escapeHtml(subject.toolName || 'modify')}</div>
    `;
  } else {
    block.innerHTML = `
      <div style="margin-bottom:8px;"><strong>Tool:</strong> ${escapeHtml(subject.toolName || 'unknown')}</div>
    `;
  }
  el.appendChild(block);

  const actions = document.createElement('div');
  actions.className = 'ask-actions';

  const approveBtn = document.createElement('button');
  approveBtn.className = 'hitl';
  approveBtn.textContent = '✓ Approve';
  approveBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'approve' });
    badge.className = 'approval-badge responding';
    badge.textContent = 'responding';
    actions.querySelectorAll('button').forEach(b => b.disabled = true);
    addChat('user', '→ Approved', runId, 'human_resolved');
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(approveBtn);

  const denyBtn = document.createElement('button');
  denyBtn.className = 'danger';
  denyBtn.textContent = '✗ Deny';
  denyBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'deny' });
    badge.className = 'approval-badge responding';
    badge.textContent = 'responding';
    actions.querySelectorAll('button').forEach(b => b.disabled = true);
    addChat('user', '→ Denied', runId, 'human_resolved');
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(denyBtn);

  el.appendChild(actions);

  applyChatFilter(el);
  $('chat').append(el);
  scrollChat();
}

// ---- ModeSwitch Renderer (ExitPlanMode) ----

function renderModeSwitchBanner(interactionId, runId, sessionKey, data) {
  const banner = $('hitlBanner');
  banner.style.display = 'block';
  banner.innerHTML = '';
  banner.className = 'hitl-banner';
  banner.dataset.interactionId = interactionId;

  const title = document.createElement('div');
  title.className = 'title';
  title.textContent = `${getInteractionIcon(data.kind)} ${data.title || getInteractionTitle(data.kind)}`;
  banner.appendChild(title);

  if (data.description) {
    const desc = document.createElement('div');
    desc.style.cssText = 'font-size:12px;color:var(--muted);margin-bottom:10px;';
    desc.textContent = data.description;
    banner.appendChild(desc);
  }

  const subject = data.subject || {};
  const infoDiv = document.createElement('div');
  infoDiv.className = 'hitl-question';
  infoDiv.innerHTML = `
    <div style="font-size:13px;margin-bottom:8px;">
      <strong>From:</strong> <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px;">${escapeHtml(subject.fromMode || 'plan')}</code>
      →
      <strong>To:</strong> <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px;">${escapeHtml(subject.toMode || 'execute')}</code>
    </div>
  `;
  banner.appendChild(infoDiv);

  const btnRow = document.createElement('div');
  btnRow.className = 'btns';

  const proceedBtn = document.createElement('button');
  proceedBtn.className = 'hitl';
  proceedBtn.textContent = '✓ Proceed';
  proceedBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'proceed' });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(proceedBtn);

  const stayBtn = document.createElement('button');
  stayBtn.className = 'secondary';
  stayBtn.textContent = 'Stay';
  stayBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'stay' });
    dismissInteractionBanner(interactionId);
  };
  btnRow.appendChild(stayBtn);

  banner.appendChild(btnRow);
}

function renderModeSwitchInline(interactionId, runId, sessionKey, data) {
  const el = document.createElement('div');
  el.className = 'msg human_input';
  el.dataset.runId = runId || '';
  el.dataset.msgType = 'human_input';
  el.dataset.interactionId = interactionId;

  const meta = document.createElement('div');
  meta.className = 'meta';
  const badge = document.createElement('span');
  badge.className = 'approval-badge waiting';
  badge.textContent = 'waiting';
  meta.innerHTML = `<span style="margin-right:4px;">${getInteractionIcon(data.kind)}</span> ${data.kind}${runId ? ` · ${runId.slice(0, 8)}` : ''} `;
  meta.appendChild(badge);
  el.appendChild(meta);

  if (data.title) {
    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-weight:700;margin-bottom:8px;color:var(--accent-2);';
    titleEl.textContent = data.title;
    el.appendChild(titleEl);
  }

  const subject = data.subject || {};
  const block = document.createElement('div');
  block.className = 'ask-block';
  block.innerHTML = `
    <div style="font-size:13px;margin-bottom:8px;">
      <strong>From:</strong> <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px;">${escapeHtml(subject.fromMode || 'plan')}</code>
      →
      <strong>To:</strong> <code style="background:var(--panel-2);padding:2px 6px;border-radius:4px;">${escapeHtml(subject.toMode || 'execute')}</code>
    </div>
  `;
  el.appendChild(block);

  const actions = document.createElement('div');
  actions.className = 'ask-actions';

  const proceedBtn = document.createElement('button');
  proceedBtn.className = 'hitl';
  proceedBtn.textContent = '✓ Proceed';
  proceedBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'proceed' });
    badge.className = 'approval-badge responding';
    badge.textContent = 'responding';
    actions.querySelectorAll('button').forEach(b => b.disabled = true);
    addChat('user', '→ Proceed', runId, 'human_resolved');
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(proceedBtn);

  const stayBtn = document.createElement('button');
  stayBtn.className = 'secondary';
  stayBtn.textContent = 'Stay';
  stayBtn.onclick = () => {
    send('interaction.resolve', { interactionId, decision: 'stay' });
    badge.className = 'approval-badge responding';
    badge.textContent = 'responding';
    actions.querySelectorAll('button').forEach(b => b.disabled = true);
    addChat('user', '→ Stay', runId, 'human_resolved');
    dismissInteractionBanner(interactionId);
  };
  actions.appendChild(stayBtn);

  el.appendChild(actions);

  applyChatFilter(el);
  $('chat').append(el);
  scrollChat();
}

// ---- Unified Interaction Dispatcher ----

function renderInteractionBanner(interactionId, runId, sessionKey, data) {
  switch (data.kind) {
    case 'ask_user':
      renderAskUserBanner(interactionId, runId, sessionKey, data);
      break;
    case 'confirm':
      renderConfirmBanner(interactionId, runId, sessionKey, data);
      break;
    case 'mode_switch':
      renderModeSwitchBanner(interactionId, runId, sessionKey, data);
      break;
    default:
      console.warn('Unknown interaction kind:', data.kind);
  }
}

function renderInteractionInline(interactionId, runId, sessionKey, data) {
  switch (data.kind) {
    case 'ask_user':
      renderAskUserInline(interactionId, runId, sessionKey, data);
      break;
    case 'confirm':
      renderConfirmInline(interactionId, runId, sessionKey, data);
      break;
    case 'mode_switch':
      renderModeSwitchInline(interactionId, runId, sessionKey, data);
      break;
    default:
      console.warn('Unknown interaction kind:', data.kind);
  }
}

function dismissInteractionBanner(interactionId) {
  const banner = $('hitlBanner');
  if (interactionId) {
    const active = banner.dataset.interactionId || '';
    if (active && active !== interactionId) return;
    delete banner.dataset.interactionId;
    state.pendingInteractions.delete(interactionId);
  } else {
    delete banner.dataset.interactionId;
  }
  banner.style.display = 'none';
  banner.innerHTML = '';
}

function dismissAllBanners() {
  dismissInteractionBanner(null);
}

// ---- System Event Chat Message ----

function addSystemEvent(tag, text, runId, subClass = '') {
  const el = document.createElement('div');
  el.className = `msg assistant system_event ${subClass}`.trim();
  el.dataset.runId = runId || '';
  el.dataset.msgType = 'system_event';
  el.innerHTML = `<div class="meta">${tag}${runId ? ` · ${runId.slice(0, 8)}` : ''}</div><pre></pre>`;
  el.querySelector('pre').textContent = text;
  applyChatFilter(el);
  $('chat').append(el);
  scrollChat();
  return el;
}

// ---- Connection ----

function connect() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
  const ws = new WebSocket($('wsUrl').value);
  state.ws = ws;

  ws.onopen = () => setStatus('ok', 'Connected');
  ws.onclose = () => setStatus('err', 'Disconnected');
  ws.onerror = () => setStatus('err', 'Error');

  ws.onmessage = (event) => {
    const frame = JSON.parse(event.data);
    addEvent({ direction: 'in', ...frame });

    // connect challenge
    if (frame.type === 'event' && frame.event === 'connect.challenge') {
      send('connect', {
        minProtocol: 3, maxProtocol: 3,
        client: { id: $('clientId').value, version: $('clientVersion').value, mode: 'operator' },
        role: 'operator', scopes: ['operator.read', 'operator.write'],
      });
      return;
    }

    // runId tracking
    if (frame.type === 'res' && frame.ok && frame.payload?.runId) {
      state.currentRunId = frame.payload.runId;
      return;
    }

    // health check response
    if (frame.type === 'res' && frame.id && frame.payload?.supportsStreamJson !== undefined) {
      return;
    }

    // session responses — sync cwd
    if (frame.type === 'res' && frame.ok) {
      if (frame.payload?.cwd) syncCwd(frame.payload.cwd);
      if (Array.isArray(frame.payload?.sessions)) {
        const key = $('sessionKey').value;
        const match = frame.payload.sessions.find(s => s.key === key);
        if (match?.cwd) syncCwd(match.cwd);
      }
    }

    // chat.history response
    if (frame.type === 'res' && frame.payload?.messages) {
      $('chat').innerHTML = '';
      state.assistantByRunId.clear();
      state.thinkingByRunId.clear();
      for (const msg of frame.payload.messages) {
        addChat(msg.role === 'assistant' ? 'assistant' : (msg.role === 'user' ? 'user' : 'assistant'), msg.text, msg.runId || msg.id);
      }
      return;
    }

    // ---- interaction.requested top-level event ----
    if (frame.type === 'event' && frame.event === 'interaction.requested') {
      const p = frame.payload;
      if (!p) return;
      if (!state.pendingInteractions.has(p.interactionId)) {
        state.pendingInteractions.set(p.interactionId, {
          runId: p.runId,
          sessionKey: p.sessionKey,
          interactionId: p.interactionId,
          kind: p.kind,
          prompt: p.prompt,
          questions: p.questions,
          subject: p.subject,
          options: p.options,
        });
      }
      return;
    }

    // ---- interaction.resolved top-level event ----
    if (frame.type === 'event' && frame.event === 'interaction.resolved') {
      const p = frame.payload;
      if (!p) return;
      state.pendingInteractions.delete(p.interactionId);
      const askEl = document.querySelector(`.msg.human_input[data-interaction-id="${p.interactionId}"]`);
      if (askEl) {
        askEl.classList.add('resolved');
        const badge = askEl.querySelector('.approval-badge');
        if (badge) {
          badge.className = 'approval-badge resolved';
          badge.textContent = p.phase;
        }
      }
      const answerText = p.answer || `(${p.phase})`;
      addChat('user', `[interaction ${p.phase}] ${answerText}`, p.runId, 'human_resolved');
      dismissInteractionBanner(p.interactionId);
      return;
    }

    // ---- chat event: text only ----
    if (frame.type === 'event' && frame.event === 'chat') {
      const p = frame.payload;
      const text = p?.message?.content?.map((c) => c.text || '').join('') || '';
      if (p.state === 'delta') {
        upsertAssistant(p.runId, text, false);
      } else if (p.state === 'final') {
        upsertAssistant(p.runId, text || '(final, empty)', true, p.stopReason || 'done');
      } else if (p.state === 'error') {
        upsertAssistant(p.runId, p.errorMessage || 'unknown error', true, 'error');
      } else if (p.state === 'aborted') {
        upsertAssistant(p.runId, '(aborted)', true, 'aborted');
      }
      return;
    }

    // ---- agent event: structured ----
    if (frame.type === 'event' && frame.event === 'agent') {
      const p = frame.payload;
      if (!p) return;
      const { stream, data, runId, sessionKey } = p;

      // ===== lifecycle =====
      if (stream === 'lifecycle') {
        if (data?.phase === 'start') {
          state.thinkingByRunId.delete(runId);
          if (data.sessionId) state.currentSessionIdByRunId.set(runId, data.sessionId);
          if (data.cwd) syncCwd(data.cwd);
          setRunStatus('running');
          const parts = ['⚡ session started'];
          if (data.sessionId) parts.push(`session: ${data.sessionId}`);
          if (data.cwd) parts.push(`cwd: ${data.cwd}`);
          if (data.tools) parts.push(`tools: [${data.tools.join(', ')}]`);
          addSystemEvent('⚡ lifecycle', parts.join('\n'), runId, 'lifecycle-start');
        } else if (data?.phase === 'end') {
          state.thinkingByRunId.delete(runId);
          dismissAllBanners();
          setRunStatus('completed');
          addSystemEvent('⚡ lifecycle', `session ended (${data.stopReason || 'end_turn'})`, runId, 'lifecycle-end');
        } else if (data?.phase === 'error') {
          state.thinkingByRunId.delete(runId);
          dismissAllBanners();
          setRunStatus('error');
          addSystemEvent('⚡ lifecycle', `error: ${data.error || 'unknown'}`, runId, 'lifecycle-error');
        }
      }

      // ===== message =====
      if (stream === 'message') {
        if (data?.phase === 'start') {
          if (data.model) state.currentModelByRunId.set(runId, data.model);
          const parts = ['📧 message start'];
          if (data.model) parts.push(`model: ${data.model}`);
          if (data.messageId) parts.push(`messageId: ${data.messageId}`);
          if (data.usage) parts.push(`initial usage: inputTokens=${data.usage.inputTokens ?? 0} outputTokens=${data.usage.outputTokens ?? 0}`);
          addSystemEvent('📧 message', parts.join('\n'), runId, 'message-start');
        } else if (data?.phase === 'stop') {
          addSystemEvent('📧 message', 'message stop', runId, 'message-stop');
        }
      }

      // ===== content_block =====
      if (stream === 'content_block') {
        if (data?.phase === 'start') {
          let label = `📦 content_block #${data.index} start (${data.blockType})`;
          if (data.blockType === 'tool_use' && data.name) {
            label += ` tool=${data.name} id=${data.toolCallId || ''}`;
          }
          addSystemEvent('📦 content_block', label, runId, `block-start-${data.blockType}`);
        } else if (data?.phase === 'stop') {
          addSystemEvent('📦 content_block', `content_block #${data.index} stop (${data.blockType})`, runId, `block-stop-${data.blockType}`);
        }
      }

      // ===== thinking =====
      if (stream === 'thinking') {
        const text = data?.delta || data?.text || '';
        let el = state.thinkingByRunId.get(runId);
        if (!el) {
          el = addChat('thinking', text, runId, 'thinking');
          state.thinkingByRunId.set(runId, el);
        } else {
          const pre = el.querySelector('pre');
          pre.textContent += text;
          const hint = el.querySelector('.collapse-hint');
          if (hint && el.classList.contains('collapsed')) {
            hint.textContent = `(${pre.textContent.length} chars, click to expand)`;
          }
          scrollChat();
        }
      }

      // ===== tool =====
      if (stream === 'tool') {
        if (data?.phase === 'start') {
          if (!state.toolUpdateByRunId.has(runId)) state.toolUpdateByRunId.set(runId, new Map());
          addChat('tool', `🔧 ${data.name || 'unknown'} [start]`, runId, 'tool_use');
        } else if (data?.phase === 'update') {
          const runTools = state.toolUpdateByRunId.get(runId);
          if (runTools) runTools.set(data.toolCallId, data.partialArgs || '');
          const partialShort = (data.partialArgs || '').slice(-80);
          addChat('tool', `⏳ ${data.toolCallId?.slice(0, 12) || '?'} args: …${partialShort}`, runId, 'tool_use');
        } else if (data?.phase === 'result') {
          const inputStr = data?.result ? JSON.stringify(data.result, null, 2).slice(0, 500) : '';
          const errStr = data?.error ? `\n⚠ ${data.error}` : '';
          addChat('tool', `✅ ${data.name || 'unknown'} [result]${inputStr ? '\n' + inputStr : ''}${errStr}`, runId, 'tool_use');
        }
      }

      // ===== command_output =====
      if (stream === 'command_output') {
        if (data?.phase === 'delta' && data?.output) {
          addChat('assistant', `[cmd] ${data.output}`, runId, 'tool_use');
        } else if (data?.phase === 'end') {
          const parts = ['💻 command end'];
          if (data.toolCallId) parts.push(`tool: ${data.toolCallId.slice(0, 12)}`);
          if (data.exitCode != null) parts.push(`exitCode: ${data.exitCode}`);
          if (data.durationMs != null) parts.push(`duration: ${data.durationMs}ms`);
          if (data.cwd) parts.push(`cwd: ${data.cwd}`);
          addSystemEvent('💻 command_output', parts.join('\n'), runId, 'command-end');
        }
      }

      // ===== interaction (agent stream) =====
      if (stream === 'interaction') {
        if (data?.phase === 'requested') {
          state.pendingInteractions.set(data.interactionId, { runId, sessionKey, ...data });
          setRunStatus('waiting_human');
          renderInteractionBanner(data.interactionId, runId, sessionKey, data);
          renderInteractionInline(data.interactionId, runId, sessionKey, data);
        } else if (data?.phase === 'answered' || data?.phase === 'approved' || data?.phase === 'denied' || data?.phase === 'cancelled' || data?.phase === 'expired') {
          state.pendingInteractions.delete(data.interactionId);
          const askEl = document.querySelector(`.msg.human_input[data-interaction-id="${data.interactionId}"]`);
          if (askEl) {
            askEl.classList.add('resolved');
            const badge = askEl.querySelector('.approval-badge');
            if (badge) {
              badge.className = 'approval-badge resolved';
              badge.textContent = data.phase;
            }
          }
          const answerText = data.answer || `(${data.phase})`;
          addChat('user', `[interaction ${data.phase}] ${answerText}`, runId, 'human_resolved');
          dismissInteractionBanner(data.interactionId);
        }
      }

      // ===== assistant =====
      if (stream === 'assistant') {
        const parts = ['📊 assistant'];
        if (data.usage) {
          const u = data.usage;
          parts.push(`tokens: in=${u.inputTokens ?? '-'} out=${u.outputTokens ?? '-'} cacheR=${u.cacheReadTokens ?? '-'} cacheW=${u.cacheCreationTokens ?? '-'}`);
        }
        if (data.costUsd != null) parts.push(`cost: $${data.costUsd}`);
        if (data.durationMs != null) parts.push(`duration: ${data.durationMs}ms`);
        if (data.numTurns != null) parts.push(`turns: ${data.numTurns}`);
        if (data.model) parts.push(`model: ${data.model}`);
        if (parts.length > 1) {
          addSystemEvent('📊 assistant', parts.join('\n'), runId, 'assistant-stats');
        }
      }

      return;
    }
  };
}

// ---- Buttons ----

$('connectBtn').onclick = connect;
$('disconnectBtn').onclick = () => state.ws?.close();
$('checkClaudeBtn').onclick = () => send('health.claude', {});
$('applyCwdBtn').onclick = () => {
  const cwd = $('cwd').value.trim();
  if (!cwd) {
    alert('Please enter a workspace directory path');
    return;
  }
  send('sessions.patch', { key: $('sessionKey').value, cwd });
  state.currentCwd = cwd;
  const status = $('cwdStatus');
  if (status) status.textContent = `Applying: ${cwd}…`;
};
$('sendBtn').onclick = () => {
  const msg = $('message').value.trim();
  if (!msg) return;
  addChat('user', msg, 'local');
  const mode = $('mode')?.value || '';
  const params = {
    sessionKey: $('sessionKey').value,
    message: msg,
    cwd: $('cwd').value.trim(),
  };
  if (mode) params.mode = mode;
  send('chat.send', params);
};
$('historyBtn').onclick = () => send('chat.history', { sessionKey: $('sessionKey').value, limit: 20 });
$('abortBtn').onclick = () => send('chat.abort', { sessionKey: $('sessionKey').value, runId: state.currentRunId });
$('clearChatBtn').onclick = () => {
  $('chat').innerHTML = '';
  state.assistantByRunId.clear();
  state.thinkingByRunId.clear();
  state.currentModelByRunId.clear();
  state.currentSessionIdByRunId.clear();
  state.toolUpdateByRunId.clear();
  state.pendingInteractions.clear();
  dismissAllBanners();
};
$('clearEventBtn').onclick = () => { $('events').innerHTML = ''; };
$('newSessionBtn').onclick = () => {
  const key = `agent:main:${crypto.randomUUID().slice(0, 8)}`;
  $('sessionKey').value = key;
  const cwd = $('cwd').value.trim();
  send('session.new', { sessionKey: key, cwd: cwd || undefined });
  if (cwd) state.currentCwd = cwd;
  $('chat').innerHTML = '';
  state.assistantByRunId.clear();
  state.thinkingByRunId.clear();
  state.currentModelByRunId.clear();
  state.currentSessionIdByRunId.clear();
  state.toolUpdateByRunId.clear();
  state.pendingInteractions.clear();
  dismissAllBanners();
};

// Enter to send
$('message').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    $('sendBtn').click();
  }
});

// ---- Test Interaction Simulators ----

function simulateInteractionEvent(kind, data) {
  const interactionId = `int:test:${Date.now()}`;
  const runId = state.currentRunId || `run:test:${Date.now()}`;
  const sessionKey = $('sessionKey').value || 'session:test:default';

  const event = {
    type: 'event',
    event: 'agent',
    payload: {
      runId,
      sessionKey,
      seq: Date.now(),
      stream: 'interaction',
      ts: Date.now(),
      data: {
        phase: 'requested',
        interactionId,
        kind,
        ...data,
        createdAtMs: Date.now(),
        expiresAtMs: Date.now() + 300000,
      },
    },
    seq: state.reqId++,
  };

  addEvent({ direction: 'in', ...event });

  const payloadData = event.payload.data;
  state.pendingInteractions.set(payloadData.interactionId, { runId, sessionKey, ...payloadData });
  setRunStatus('waiting_human');
  renderInteractionBanner(payloadData.interactionId, runId, sessionKey, payloadData);
  renderInteractionInline(payloadData.interactionId, runId, sessionKey, payloadData);
}

// AskUserQuestion tests
$('testSelectBtn').onclick = () => {
  simulateInteractionEvent('ask_user', {
    title: 'Claude needs your input',
    description: 'Please answer the following question(s)',
    prompt: '请选择一个数据库类型：',
    subject: { type: 'tool', toolName: 'AskUserQuestion', toolCallId: 'toolu:test:001' },
    questions: [
      {
        question: '请选择一个数据库类型：',
        header: '数据库选择',
        options: [
          { label: 'PostgreSQL', description: '关系型数据库，适合复杂查询' },
          { label: 'MongoDB', description: '文档数据库，灵活的schema' },
          { label: 'Redis', description: '内存数据库，高速缓存' },
        ],
      },
    ],
    inputSchema: { type: 'choices', multiSelect: false },
    uiHints: { variant: 'question', severity: 'info' },
  });
};

$('testMultiSelectBtn').onclick = () => {
  simulateInteractionEvent('ask_user', {
    title: 'Claude needs your input',
    description: 'Please answer the following question(s)',
    prompt: '请选择需要安装的依赖（可多选）：',
    subject: { type: 'tool', toolName: 'AskUserQuestion', toolCallId: 'toolu:test:002' },
    questions: [
      {
        question: '请选择需要安装的依赖（可多选）：',
        header: '依赖选择',
        multiSelect: true,
        options: [
          { label: 'TypeScript', description: 'TypeScript 支持' },
          { label: 'ESLint', description: '代码检查' },
          { label: 'Prettier', description: '代码格式化' },
          { label: 'Jest', description: '单元测试' },
        ],
      },
    ],
    inputSchema: { type: 'choices', multiSelect: true },
    uiHints: { variant: 'question', severity: 'info' },
  });
};

$('testInputBtn').onclick = () => {
  simulateInteractionEvent('ask_user', {
    title: 'Claude needs your input',
    description: 'Please answer the following question(s)',
    prompt: '请输入项目信息：',
    subject: { type: 'tool', toolName: 'AskUserQuestion', toolCallId: 'toolu:test:003' },
    questions: [
      { question: '请输入项目名称：', header: '项目配置' },
      { question: '请输入项目描述：' },
    ],
    inputSchema: { type: 'text', multiSelect: false },
    uiHints: { variant: 'question', severity: 'info' },
  });
};

$('testMixedBtn').onclick = () => {
  simulateInteractionEvent('ask_user', {
    title: 'Claude needs your input',
    description: 'Please answer the following question(s)',
    prompt: '项目配置：',
    subject: { type: 'tool', toolName: 'AskUserQuestion', toolCallId: 'toolu:test:004' },
    questions: [
      {
        question: '选择项目类型：',
        header: '项目设置',
        options: [
          { label: 'Web 应用', description: '前端 Web 应用' },
          { label: 'CLI 工具', description: '命令行工具' },
          { label: 'API 服务', description: '后端 API 服务' },
        ],
      },
      {
        question: '选择需要的功能（可多选）：',
        multiSelect: true,
        options: [
          { label: '认证系统', description: '用户登录/注册' },
          { label: '数据持久化', description: '数据库存储' },
          { label: '日志系统', description: '应用日志' },
        ],
      },
      { question: '请输入项目名称：' },
    ],
    inputSchema: { type: 'form', multiSelect: true },
    uiHints: { variant: 'question', severity: 'info' },
  });
};

// Chat filter toggles
$('showThinking').addEventListener('change', refreshChatFilter);
$('showTool').addEventListener('change', refreshChatFilter);
$('showSystem').addEventListener('change', refreshChatFilter);
