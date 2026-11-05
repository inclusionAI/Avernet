import React from 'react';
import { createRoot } from 'react-dom/client';
import UndercoverGamePanel from '../../src/undercover-game/UndercoverGamePanel';
import { UndercoverGameOpsPanel } from '../../src/undercover-game/themes/ops';

const query = new URLSearchParams(location.search);
const theme = query.get('theme') ?? 'club';
const mode = query.get('mode') ?? 'observe';
const finale = mode === 'finale' || mode === 'finished';
const width = Math.max(280, Math.min(1000, Number(query.get('width')) || 320));
const height = Math.max(280, Math.min(1000, Number(query.get('height')) || 600));
const bubbleSeat = Number(query.get('bubbleSeat')) || 5;
let submitted = false;

const pk = mode.startsWith('pk-');
const voting = mode === 'vote' || mode === 'pk-vote' || finale;
const eliminated = mode === 'eliminated';
const players = [
  { actorId: 'a', displayName: '话痨小满', seatNumber: 1 },
  { actorId: 'human', displayName: '你', seatNumber: 2, isHuman: true },
  { actorId: 'c', displayName: '稳健老陈', seatNumber: 3 },
  { actorId: 'd', displayName: '戏精阿浪', seatNumber: 4 },
  { actorId: 'e', displayName: '和事佬阿和', seatNumber: 5 },
  { actorId: 'f', displayName: '逻辑控林工', seatNumber: 6 },
].map((p) => (eliminated && p.actorId === 'e' ? { ...p, alive: false, eliminated: true } : p));

const params = {
  runId: 'preview-run', groupId: 'preview-group', sessionId: 'preview-session', gameSessionId: 'preview-game',
  phase: pk ? (voting ? 'pk_voting' : 'pk_speaking') : mode === 'eliminated' ? 'speaking' : finale ? 'voting' : (mode === 'vote' || mode === 'finished' ? 'voting' : 'speaking'),
  resultFile: finale ? 'undercover-result-v1-r1-a1.json' : undefined,
  round: 1, attempt: 1,
  host: { actorId: 'host', displayName: '主持人' },
  players,
  seatOrder: players.map((p) => p.actorId),
  turnOrder: pk ? (voting ? players.map((p) => p.actorId) : ['human', 'c']) : players.map((p) => p.actorId),
  nodeActorMap: pk ? {
    ...Object.fromEntries(players.map((p) => [`speech-${p.actorId}`, p.actorId])),
    'vote-human': 'human', 'vote-c': 'c', 'tally': 'host',
    ...(voting ? {} : { 'speech-human': 'human', 'speech-c': 'c' }),
  } : {
    ...Object.fromEntries(players.map((p) => [`speech-${p.actorId}`, p.actorId])),
    ...(mode === 'vote' || finale ? {
      ...Object.fromEntries(players.map((p) => [`vote-${p.actorId}`, p.actorId])),
      'tally': 'host',
    } : {}),
  },
  currentViewerActorId: 'human',
  rules: { speechMaxChars: 25, forbidOwnWord: true, bluntness: mode === 'eliminated' ? 5 : undefined },
  publicHistory: [],
  voteCandidates: voting
    ? players.filter((p) => p.actorId !== 'human').map((p) => ({ ...p, eligible: true })) : undefined,
  autoRefresh: false,
  style: { height: '100%' },
};

if (pk) {
  Object.assign(params, {
    pkCandidates: ['human', 'c'],
    voteHistory: [{
      round: 1, stage: 'regular',
      votes: [
        { seatNumber: 1, displayName: '话痨小满', text: '我投2号' },
        { seatNumber: 2, displayName: '你', text: '我投3号' },
        { seatNumber: 3, displayName: '稳健老陈', text: '我投2号' },
        { seatNumber: 5, displayName: '和事佬阿和', text: '我投3号' },
      ],
      counts: [{ seatNumber: 2, votes: 2 }, { seatNumber: 3, votes: 2 }],
    }],
  });
}

const response = (data: unknown) => new Response(JSON.stringify({ code: 20000, data }), { headers: { 'content-type': 'application/json' } });

globalThis.fetch = async (url, init) => {
  if (init?.method === 'POST') { submitted = true; return response({ accepted: true }); }
  const path = new URL(String(url), location.href).pathname;
  if (path.endsWith('/graph')) {
    const baseNodes = pk
      ? [
          { node_id: voting ? 'vote_start' : 'speak-human', status: 'completed' },
          ...(voting
            ? [
                { node_id: 'vote-human', status: 'running' },
                { node_id: 'vote-c', status: 'completed' },
                { node_id: 'tally', status: 'pending' },
              ]
            : [
                { node_id: 'speech-c', status: 'pending' },
                { node_id: 'tally', status: 'pending' },
              ]),
        ]
      : mode === 'vote' || finale ? [
          { node_id: 'vote_start', status: 'completed' },
          ...players.map((p) => ({ node_id: `vote-${p.actorId}`, status: 'completed' })),
          { node_id: 'tally', status: finale ? 'completed' : 'pending' },
        ] : [
          ...players.map((p) => ({ node_id: `speech-${p.actorId}`, status: 'completed' })),
          { node_id: 'collect', status: 'completed' },
        ];
    return response({
      run: { run_id: 'preview-run', status: finale ? 'completed' : 'running', created_at: 1, updated_at: 2 },
      nodes: baseNodes.map((n) => ({ ...n, attempt: 1 })),
      edges: pk && voting ? [
        { source: 'vote_start', target: 'vote-human' },
        { source: 'vote_start', target: 'vote-c' },
        { source: 'vote-human', target: 'tally' },
        { source: 'vote-c', target: 'tally' },
      ] : mode === 'vote' || finale ? [
        ...players.map((p) => ({ source: 'vote_start', target: `vote-${p.actorId}` })),
        ...players.map((p) => ({ source: `vote-${p.actorId}`, target: 'tally' })),
      ] : [
        ...players.slice(0, -1).map((p, i) => ({ source: `speech-${p.actorId}`, target: `speech-${players[i + 1].actorId}` })),
        { source: `speech-${players[players.length - 1].actorId}`, target: 'collect' },
      ],
    });
  }
  if (path.endsWith('/pending-human-nodes')) {
    if (mode === 'observe' || mode === 'finished' || finale || submitted || mode === 'eliminated' || mode === 'bubbles' || mode === 'markers') return response([]);
    const action = voting ? 'vote' : 'speech';
    const instruction = '[UNDERCOVER_UI_CONTEXT_V1]' + JSON.stringify(
      action === 'vote'
        ? { action: 'vote', round: 1, seatNumber: 2, word: '苹果', allowAbstain: !pk }
        : { action: 'speech', round: 1, seatNumber: 2, word: '苹果', maxChars: 25, forbidOwnWord: true, bluntness: 3 },
    ) + '[/UNDERCOVER_UI_CONTEXT_V1]';
    return response([{ node_id: action === 'vote' ? 'vote-human' : 'speech-human', attempt: 1, instruction, upstream_artifacts: action === 'speech' ? [{ node_id: 'speech-a', text: '我会在很忙的一天用它，具体细节再想想。' }] : [] }]);
  }
  if (path.endsWith('/messages')) return response([]);
  if (path.endsWith('/files')) {
    if (!finale) return response({ items: [], total: 0 });
    return response({ items: [{ file_id: 'result', session_id: 'preview-session', file_name: params.resultFile, status: 'Ready', size: 600, owner: { actor_kind: 'Bot', actor_id: 'host' } }], total: 1 });
  }
  if (path.includes('/files/result/content') && finale) {
    return response({ kind: 'undercover.game-result', version: 1, status: 'finished', stage: 'regular', gameSessionId: 'preview-game', hostActorId: 'host', round: 1, attempt: 1, winner: 'civilian', reason: '卧底在第一轮表现异常', summary: '平民胜利！\n词对：平民 苹果 · 卧底 梨\n第一轮全员齐发言，话痨小满的表述被多数人识破。' });
  }
  const nodeId = path.split('/').at(-1);
  const seat = players.findIndex((player) => `node-${player.actorId}` === nodeId || `speech-${player.actorId}` === nodeId || `vote-${player.actorId}` === nodeId) + 1;
  const text = (mode === 'bubbles' || mode === 'markers') && seat
    ? `${seat}号的线索：我通常会在忙碌的一天结束之后用到它，具体细节还要再想一想。`
    : nodeId === 'host' || nodeId === 'tally' || nodeId === 'collect'
      ? (finale ? '🎉 游戏结束！平民胜利！\n\n🏆 终局揭秘\n平民词：苹果 · 卧底词：梨\n共 6 人，卧底在 1 号。'
        : pk ? '你（2号）和稳健老陈（3号）最高票并列，进入 PK。' : '第一轮，六个人都在。请按顺序发言。')
      : undefined;
  return response({ node: { node_id: nodeId, run_id: 'preview-run', status: 'completed', attempt: 1, artifact_text: text, completed_at: seat === bubbleSeat ? 100 : seat } });
};

const Panel = theme === 'ops' ? UndercoverGameOpsPanel : UndercoverGamePanel;
createRoot(document.getElementById('root')!).render(
  <div className="frame" style={{ width, height }}>
    <Panel {...params} />
  </div>,
);