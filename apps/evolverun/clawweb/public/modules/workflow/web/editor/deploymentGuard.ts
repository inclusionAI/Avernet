// A browser-local warning, not a server lock: another browser or tab can still
// race this check. Never store deployment payloads or credentials here.
const prefix = 'clawweb.autoheal.review-blocked.v1.'
const keyFor = (workflowId: string) => prefix + encodeURIComponent(workflowId)

export const deploymentBlockedMessage = '本浏览器存在未确认的部署，或无法访问浏览器存储；请人工核对 Git 和数据库，勿重试。此标记不阻止其他浏览器或标签页的部署。'

export function assertDeploymentAvailable(workflowId: string): void {
  try {
    if (localStorage.getItem(keyFor(workflowId)) !== null) throw new Error('blocked')
  } catch {
    throw new Error(deploymentBlockedMessage)
  }
}

export function markDeploymentPending(workflowId: string): void {
  assertDeploymentAvailable(workflowId)
  try {
    localStorage.setItem(keyFor(workflowId), '1')
    if (localStorage.getItem(keyFor(workflowId)) !== '1') throw new Error('write failed')
  } catch {
    throw new Error('无法记录待核对标记，部署未发送；请检查浏览器存储。')
  }
}

// Call only after validating a successful response for the intended workflow.
export function clearConfirmedDeployment(workflowId: string): void {
  try {
    localStorage.removeItem(keyFor(workflowId))
    if (localStorage.getItem(keyFor(workflowId)) !== null) throw new Error('remove failed')
  } catch {
    throw new Error('部署已确认成功，但无法清除浏览器核对标记；后续部署仍受阻。')
  }
}
