import { captureTaskPanelOrigin, navigateTaskPanelConversation, restoreTaskPanelOrigin } from './taskPanelConversationAdapter';
import { getCapabilities } from '@/capabilities';
import { extractLoginUrl, isAceLoginResponse } from '@/services/backendApi/aceLoginBody';
import { triggerAceLoginRedirect } from '@/services/backendApi/httpClient';
import { chatBridge } from '@/services/workspace/chatBridge';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import { TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskConversationNavigationStore } from '@/stores/taskConversationNavigationStore';
import { taskPanelArtifacts } from './taskPanelArtifactAdapter';
import { loadTaskPanelAsset } from './taskPanelAssetLoader';
import type { ConversationRef, TaskPanelDeclaration, TaskPanelHost, TaskPanelController } from '@avernet-assets/task-panel';

function viewerId(): string | null {
  const human = getCapabilities().getHumanIdentity();
  return human.status === 'available' ? human.value?.userId?.trim() || null : null;
}
const emptyHistory: TaskPanelHost['history'] = { readPage: async () => { throw new Error('会话历史尚未就绪，请重试。'); } };
const defaultPresent: TaskPanelHost['present'] = tab => { chatBridge.openPanelTab({ ...tab, defaultWidth: resolveTaskPanelDefaultWidth() }); };
let conversation: ConversationRef | null = null;
let controller: TaskPanelController | null = null;
let loading: Promise<TaskPanelController> | null = null;
let ownerViewerId: string | null = null;
let generation = 0;
let observedViewerId: string | null = null;
const lifecycleListeners = new Set<() => void>();
export const getTaskPanelRuntimeGeneration = () => generation;
let unsubscribeSnapshot: (() => void) | null = null;
let history = emptyHistory;
let present = defaultPresent;
let navigate: TaskPanelHost['navigate'] = navigateTaskPanelConversation;
let preserving = false;
let preserve: TaskPanelHost['preservePanel'] = value => { preserving = value; };
let captureOrigin: TaskPanelHost['captureOrigin'] = captureTaskPanelOrigin;
let restoreOrigin: TaskPanelHost['restoreOrigin'] = restoreTaskPanelOrigin;
let interaction: TaskPanelHost['recordInteraction'] = () => {};
// A pending SDK presentation event, not a second task-selection/nav state machine.
let presentation: { viewerId: string; conversation: ConversationRef; tab: TaskPanelDeclaration } | null = null;
export const nextgenTaskPanelHost: TaskPanelHost = {
  getViewer: () => { const id = viewerId(); return id ? { userId: id } : null; },
  getConversation: () => conversation,
  transport: { async requestJson(path, signal) {
    const response = await fetch(path, { credentials: 'include', signal });
    if (!response.ok) throw Object.assign(new Error(`请求失败（${response.status}）`), { status: response.status });
    const body: unknown = await response.json();
    if (isAceLoginResponse(body)) { triggerAceLoginRedirect(extractLoginUrl(body)); throw Object.assign(new Error('登录状态已失效。'), { name: 'AbortError', status: 401 }); }
    return body;
  } },
  artifacts: taskPanelArtifacts, history: { readPage: (...args) => history.readPage(...args) },
  present: (...args) => present(...args), navigate: (...args) => navigate(...args),
  preservePanel: value => { preserving = value; preserve(value); }, captureOrigin: () => captureOrigin(), restoreOrigin: (...args) => restoreOrigin(...args),
  recordInteraction: record => interaction(record), taskApiBase: TASK_READ_API_BASE,
};
export function bindTaskPanelInteraction(callback: TaskPanelHost['recordInteraction']) {
  interaction = callback;
  return () => { if (interaction === callback) interaction = () => {}; };
}
export function peekTaskPanelController(): TaskPanelController | null { return controller; }
/** Identity loss is never a navigation handoff. Invalidate pending asset loads as well. */
export function resetTaskPanelRuntime() {
  generation++; observedViewerId = viewerId();
  const previous = controller; controller = null; loading = null; ownerViewerId = null;
  previous?.dispose(); unsubscribeSnapshot?.(); unsubscribeSnapshot = null;
  conversation = null; history = emptyHistory; present = defaultPresent; navigate = navigateTaskPanelConversation;
  presentation = null; preserving = false; interaction = () => {};
  preserve = value => { preserving = value; }; captureOrigin = captureTaskPanelOrigin;
  restoreOrigin = restoreTaskPanelOrigin;
  useTaskSessionAssociationStore.getState().reset();
  useTaskSidePanelStore.getState().resetAll();
  useTaskConversationNavigationStore.getState().resetAll();
  lifecycleListeners.forEach(listener => listener());
}
let watchingViewer = false;
/** Importing the task adapter must not subscribe the whole application before it is used. */
function watchViewerLifetime() {
  if (watchingViewer) return;
  watchingViewer = true;
  useWorkspaceStore.subscribe(() => {
    const nextViewer = viewerId();
    const observedChange = observedViewerId !== nextViewer; observedViewerId = nextViewer;
    if ((ownerViewerId && ownerViewerId !== nextViewer) || (observedChange && lifecycleListeners.size > 0)) resetTaskPanelRuntime();
  });
}
export function subscribeTaskPanelRuntime(listener: () => void) { watchViewerLifetime(); if (lifecycleListeners.size === 0 && !ownerViewerId) observedViewerId = viewerId(); lifecycleListeners.add(listener); return () => { lifecycleListeners.delete(listener); }; }
export async function getTaskPanelController(): Promise<TaskPanelController> {
  const userId = viewerId();
  if (ownerViewerId && ownerViewerId !== userId) resetTaskPanelRuntime();
  if (!userId) throw new Error('登录状态已失效，请重新登录。');
  watchViewerLifetime();
  if (controller) return controller;
  if (!loading) {
    ownerViewerId = userId; observedViewerId = userId;
    const revision = generation;
    const pending = loadTaskPanelAsset().then(asset => {
      if (revision !== generation || viewerId() !== userId) throw Object.assign(new Error('任务副屏身份已失效。'), { name: 'AbortError' });
      const active = asset.createTaskPanelController(nextgenTaskPanelHost);
      controller = active;
      // Legacy selectors are output-only projections. Commands never flow back from this mirror.
      unsubscribeSnapshot = active.subscribe(() => {
        const snapshot = active.getSnapshot();
        useTaskSidePanelStore.setState({ activeTaskId: snapshot.activeTaskId });
        if (snapshot.conversation) {
          const associations = useTaskSessionAssociationStore.getState();
          associations.setActiveSession(snapshot.conversation.sessionId);
          associations.setActiveSessionTaskIds(snapshot.conversation.sessionId, [...snapshot.associatedTaskIds]);
        } else useTaskSessionAssociationStore.getState().reset();
      });
      return active;
    }).catch(error => { if (revision === generation) loading = null; throw error; });
    loading = pending;
  }
  return loading;
}
/** Bind actual SDK presentation evidence to the authenticated route, never to a bare global task id. */
export function registerTaskPanelPresentation(tab: TaskPanelDeclaration): () => void {
  const userId = viewerId(); const origin = captureTaskPanelOrigin();
  if (!userId || !origin) return () => {};
  const event = { viewerId: userId, conversation: origin, tab };
  presentation = event;
  useTaskConversationNavigationStore.getState().rememberTaskTab(tab.params.taskId, tab);
  void getTaskPanelController().then(active => {
    if (presentation !== event || viewerId() !== userId || controller !== active) return;
    if (active.getSnapshot().conversation?.sessionId === origin.sessionId) { active.adoptPresentedTask(tab); return; }
    // An actual SDK mount may precede the visible-history bootstrap entirely.
    // Bind its route without scanning; stale source-route cleanup must not release this target.
    if (conversation?.sessionId !== origin.sessionId) { history = emptyHistory; present = defaultPresent; }
    conversation = origin;
    return active.enterConversation(origin, tab, { recover: false });
  }).catch(() => { /* The mounted asset boundary reports load/authentication failures. */ });
  return () => { if (presentation === event) presentation = null; };
}
export function getPresentedTaskForConversation(ref: ConversationRef): TaskPanelDeclaration | null {
  return presentation?.viewerId === viewerId() && presentation.conversation.sessionId === ref.sessionId &&
    presentation.conversation.kind === ref.kind ? presentation.tab : null;
}
export function configureTaskPanelSession(ref: ConversationRef, options: {
  history: TaskPanelHost['history']; present?: TaskPanelHost['present']; navigate?: TaskPanelHost['navigate'];
  preserve?: TaskPanelHost['preservePanel']; captureOrigin?: TaskPanelHost['captureOrigin']; restoreOrigin?: TaskPanelHost['restoreOrigin'];
}) {
  conversation = ref; history = options.history;
  if (options.present) present = options.present;
  if (options.navigate) navigate = options.navigate;
  if (options.preserve) preserve = options.preserve;
  if (options.captureOrigin) captureOrigin = options.captureOrigin;
  if (options.restoreOrigin) restoreOrigin = options.restoreOrigin;
}
export function releaseTaskPanelSession(sessionId: string, expectedGeneration = generation) {
  if (expectedGeneration !== generation) return;
  if (ownerViewerId && ownerViewerId !== viewerId()) { resetTaskPanelRuntime(); return; }
  if (conversation?.sessionId !== sessionId || preserving) return;
  conversation = null; history = emptyHistory; present = defaultPresent; controller?.leaveConversation();
}
