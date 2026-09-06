export type FullSortKey = 'completionAsc' | 'runDesc' | 'machineAsc' | 'healDesc' | 'deployDesc' | 'recentDeployDesc'

export const FULL_SORT_LABELS: Record<FullSortKey, string> = {
  completionAsc: '运行成功率 升序(短板优先)',
  runDesc: '运行数 降序',
  machineAsc: '机器耗时 升序(最快)',
  healDesc: '自愈次数 降序',
  deployDesc: '部署次数 降序',
  recentDeployDesc: '最近部署 降序',
}
