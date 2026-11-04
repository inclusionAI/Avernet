type MockResponse = {
  send: (value: unknown) => void;
  status: (code: number) => MockResponse;
};

type MockRequest = {
  query?: Record<string, string>;
};

const ok = (data: unknown) => ({ code: 200000, message: 'success', data });
const taskNotFound = (response: MockResponse) =>
  response.status(404).send({ code: 404000, message: 'task session not found' });

/**
 * Second-leg relay task lookup for the local workspace collaboration deep link.
 * The task panel opens the full graph for the mapped task id; the node id is
 * not used by the panel and therefore mirrors the root node convention.
 */
const relayTasksBySession: Record<string, { task_id: string; owner_user_id: string; node_id: string }> = {
  'bcs_grp_2e7f631b4339490db9e552ed517d29be:d8c1340a': {
    task_id: '08bf5459-daef-423e-a233-910c890aff12',
    owner_user_id: '35983',
    node_id: '08bf5459-daef-423e-a233-910c890aff12',
  },
};

const bySession = (request: MockRequest, response: MockResponse) => {
  const taskId = relayTasksBySession[request.query?.session_id ?? ''];
  return taskId ? response.send(ok(taskId)) : taskNotFound(response);
};

export default {
  'GET /openapi/v1/collaboration/tasks/by_session': bySession,
  'GET /api/v1/collaboration/tasks/by_session': bySession,
};
