import { useCallback, useEffect, useState } from 'react'
import type { DeployHistoryItem } from '@avernet/clawweb-shared/web/types'
import { packManifest } from '../editor/packCanvas'
import type { Snapshot } from '../editor/packVersionSession'
import type { ActiveHistoryChoice, ActiveHistoryEvidence } from './ActiveHistoryMismatchReview'

type ActiveHistoryState = { evidence: ActiveHistoryEvidence | null; choice: ActiveHistoryChoice }

/** This is a warning surface, not a publication identity check. Active Git and DB
 * row identities are already verified before the caller reaches this hook. */
export function useActiveHistoryMismatchReview(workflowId: string | null) {
  const [state, setState] = useState<ActiveHistoryState>({ evidence: null, choice: 'database' })
  useEffect(() => { setState({ evidence: null, choice: 'database' }) }, [workflowId])
  const prepare = useCallback((active: Snapshot | null, latestDeploy: DeployHistoryItem | null, databaseText: string | null) => {
    let evidence: ActiveHistoryEvidence | null = null
    if (workflowId && active?.activeHistoryMismatch === true && latestDeploy
      && active.baseRef === `refs/tags/deploy/${workflowId}/#${latestDeploy.deployNumber}`) {
      let gitPath = ''
      let gitYaml: string | null = null
      try {
        const location = packManifest(active.versionFiles, workflowId)
        const file = active.versionFiles.find(item => item.path === location.path)
        gitPath = location.path
        if (file) gitYaml = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(Uint8Array.from(atob(file.contentBase64), char => char.charCodeAt(0)))
      } catch { /* The warning remains visible when raw YAML cannot be decoded. */ }
      evidence = { workflowId, deployNumber: latestDeploy.deployNumber, gitPath, databaseText, gitYaml }
    }
    setState({ evidence, choice: 'database' })
  }, [workflowId])
  const choose = useCallback((choice: ActiveHistoryChoice) => { setState(previous => ({ ...previous, choice })) }, [])
  const displayText = useCallback((databaseText: string | null | undefined) =>
    state.choice === 'git' && state.evidence?.workflowId === workflowId && state.evidence.gitYaml !== null
      ? state.evidence.gitYaml
      : databaseText, [state, workflowId])
  return { evidence: state.evidence, choice: state.choice, prepare, choose, displayText }
}
