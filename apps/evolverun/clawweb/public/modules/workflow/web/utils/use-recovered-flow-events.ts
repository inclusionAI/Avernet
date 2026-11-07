import { useQuery } from '@tanstack/react-query'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { FlowEvent } from '@avernet/clawweb-shared/web/types'

// The events route silently defaults to the latest 200 records. Recovery is
// still best-effort, but explicitly use the server's maximum window so older
// node evidence is not dropped merely because the run emitted many events.
const RECOVERED_NODE_EVENT_LIMIT = 1000

export function useRecoveredFlowEvents(flowId: string) {
  return useQuery({
    queryKey: ['recovered-node-events', flowId],
    queryFn: () => fetchJson<FlowEvent[]>(
      `/api/runs/${encodeURIComponent(flowId)}/events?limit=${RECOVERED_NODE_EVENT_LIMIT}`,
    ),
    enabled: !!flowId,
  })
}
