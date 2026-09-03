import type { BotDomain } from '@/services/botWorkshop';
import { Power, RefreshCw, Server, Trash2 } from 'lucide-react';
import type { ReactNode } from 'react';

export type BotCardManagementAction = 'delete' | 'restart' | 'engine_restart' | 'upgrade';

export const lifecycleLabel: Record<BotDomain['lifecycle'], string> = {
  draft: '草稿',
  deploying: '部署中',
  prestable: '预发',
  running: '运行中',
  offline: '已下线',
  failed: '创建失败',
  unknown: '状态未知',
};

export const actionLabel: Record<BotCardManagementAction, string> = {
  delete: '删除',
  restart: '重启 Bot',
  engine_restart: '重启引擎',
  upgrade: '开启服务化',
};

export const actionIcon: Record<BotCardManagementAction, ReactNode> = {
  delete: <Trash2 className="size-4" />,
  restart: <RefreshCw className="size-4" />,
  engine_restart: <Power className="size-4" />,
  upgrade: <Server className="size-4" />,
};
