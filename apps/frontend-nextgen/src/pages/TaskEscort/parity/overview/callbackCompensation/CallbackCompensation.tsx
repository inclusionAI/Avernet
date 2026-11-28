import { getCapabilities } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { callbackCompensationErrorStatus } from '@/domain/taskEscortParity/callbackCompensationErrors';
import type { CallbackCompensationCapabilities } from '@/domain/taskEscortParity/callbackCompensationTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useCallbackCompensation } from '@/hooks/taskEscortParity/callbackCompensation/useCallbackCompensation';
import { callbackCompensationService } from '@/services/taskEscortParity/callbackCompensationService';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useIdentityStore } from '@/stores/identityStore';
import { BellRing } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { CallbackCompensationDialog } from './CallbackCompensationDialog';

function Authorized({
  context,
  capabilities,
  visible,
  onDenied,
}: {
  context: TaskEscortParityContext;
  capabilities: CallbackCompensationCapabilities;
  visible: boolean;
  onDenied: () => void;
}) {
  const state = useCallbackCompensation(context, capabilities, onDenied);
  return (
    <>
      {visible && (
        <Button
          aria-label="成功回调补偿"
          size="sm"
          variant="outline"
          className="border-primary/30 bg-primary/5 text-primary hover:bg-primary/10"
          leftIcon={<BellRing aria-hidden className="size-3.5" />}
          onClick={() => state.setOpen(true)}
        >
          成功回调补偿{state.batch.batchId && <span aria-hidden className="size-1.5 rounded-full bg-primary" />}
        </Button>
      )}
      <CallbackCompensationDialog context={context} state={state} capabilities={capabilities} />
    </>
  );
}
function CapabilityEntry({ context, visible }: { context: TaskEscortParityContext; visible: boolean }) {
  const [capabilities, setCapabilities] = useState<CallbackCompensationCapabilities | null>(null);
  const [status, setStatus] = useState<'checking' | 'allowed' | 'denied' | 'unavailable'>('checking');
  const [revision, setRevision] = useState(0);
  const deny = useCallback(() => {
    setCapabilities(null);
    setStatus('denied');
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setCapabilities(null);
    setStatus('checking');
    void callbackCompensationService
      .capabilities(context.workflowId, controller.signal)
      .then((value) => {
        if (!active || controller.signal.aborted) return;
        if (value.canPreview !== true || value.workflowId !== context.workflowId) {
          deny();
          return;
        }
        setCapabilities(value);
        setStatus('allowed');
      })
      .catch((error) => {
        if (!active || controller.signal.aborted) return;
        if (callbackCompensationErrorStatus(error) === 403) deny();
        else setStatus('unavailable');
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [context.workflowId, revision, deny]);
  if (status === 'unavailable')
    return visible ? (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span role="status">补偿服务不可用</span>
        <Button size="sm" variant="ghost" onClick={() => setRevision((value) => value + 1)}>
          重试补偿权限
        </Button>
      </div>
    ) : null;
  // Never reveal receiver/config metadata on a denied response, even if the user can edit workflows.
  if (status !== 'allowed' || !capabilities) return null;
  return (
    <Authorized
      key={capabilities.environment}
      context={context}
      capabilities={capabilities}
      visible={visible}
      onDenied={deny}
    />
  );
}
/** The keyed boundary discards all UI state and aborts reads/writes before another actor/scope can render. */
export function CallbackCompensation({
  context,
  visible = true,
}: {
  context: TaskEscortParityContext;
  visible?: boolean;
}) {
  const authEpoch = useExternalAuthStore((state) => state.authEpoch);
  const authStatus = useExternalAuthStore((state) => state.status);
  const authUserId = useExternalAuthStore((state) => state.user?.userId);
  const loggingOut = useExternalAuthStore((state) => state.isLoggingOut);
  const actorId = useIdentityStore((state) => state.currentIdentityId);
  const runtime = getCapabilities().getRuntimeEnvironment();
  if (
    !context.ready ||
    !context.workflowId ||
    !context.userId?.trim() ||
    authStatus === 'unauthenticated' ||
    loggingOut
  )
    return null;
  const key = JSON.stringify([
    runtime.value,
    context.scopeKey,
    context.userId,
    context.workflowId,
    actorId,
    authEpoch,
    authStatus,
    authUserId,
  ]);
  return <CapabilityEntry key={key} context={context} visible={visible} />;
}
