import { createTaskPanelController } from '../../assets/task-panel/src/controller/createTaskPanelController';
import { useTaskSessionAssociationStore } from '@/stores/taskSessionAssociationStore';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { ConversationRef, TaskPanelHost } from '../../assets/task-panel/src/contracts';
/** Uses the real shared arbitration/recovery controller behind the host transport seam. */
export function createTestTaskPanelRuntime(options: { bySession: (id: string) => Promise<any>; list: (params: { user_id: string | null }) => Promise<any> }) {
  let conversation: ConversationRef | null = null;
  let configured: { history: TaskPanelHost['history']; present: TaskPanelHost['present'] };
  const host: TaskPanelHost = {
    getViewer: () => ({ userId: 'u1' }), getConversation: () => conversation,
    transport: { requestJson: async path => {
      const url = new URL(path, 'http://localhost');
      if (url.pathname.endsWith('/by_session')) return { code: 200000, data: { items: (await options.bySession(url.searchParams.get('session_id')!)).data ?? [] } };
      return { code: 200000, data: (await options.list({ user_id: url.searchParams.get('user_id') })).data };
    } },
    history: { readPage: (...args) => configured.history.readPage(...args) },
    artifacts: { readContent: async () => new Blob(), download: async () => {} },
    present: (...args) => configured.present(...args), navigate: async () => ({ status: 'ready' }),
    restoreOrigin: async () => ({ status: 'ready' }), captureOrigin: () => null, preservePanel: () => {}, recordInteraction: () => {},
  };
  const controller = createTaskPanelController(host);
  controller.subscribe(() => {
    const snapshot = controller.getSnapshot();
    useTaskSidePanelStore.setState({ activeTaskId: snapshot.activeTaskId });
    if (snapshot.conversation) {
      const associations = useTaskSessionAssociationStore.getState();
      associations.setActiveSession(snapshot.conversation.sessionId);
      associations.setActiveSessionTaskIds(snapshot.conversation.sessionId, [...snapshot.associatedTaskIds]);
    } else useTaskSessionAssociationStore.getState().reset();
  });
  return {
    configureTaskPanelSession: (ref: ConversationRef, value: typeof configured) => { conversation = ref; configured = value; },
    getTaskPanelController: async () => controller,
    getPresentedTaskForConversation: () => null,
    peekTaskPanelController: () => controller,
    getTaskPanelRuntimeGeneration: () => 0,
    subscribeTaskPanelRuntime: () => () => {},
    releaseTaskPanelSession: () => controller.leaveConversation(),
    bindTaskPanelInteraction: () => () => {},
  };
}
