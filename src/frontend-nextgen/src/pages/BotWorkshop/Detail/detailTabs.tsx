import type { MoreConfigTab } from '@/components/BotWorkshop/Editor/MoreConfigPanel';
import { Clock, Database, FileText, LayoutGrid, Network, Settings, ShieldCheck, Smartphone } from 'lucide-react';
import React from 'react';

export type MainTab = 'capability' | 'resource' | 'routine' | 'escort';
export const mainTabs: Array<{ key: MainTab; label: string; icon: React.ReactNode }> = [
  { key: 'capability', label: '能力集', icon: <LayoutGrid className="size-3.5 shrink-0" /> },
  { key: 'resource', label: '资源', icon: <Database className="size-3.5 shrink-0" /> },
  { key: 'routine', label: '定时任务', icon: <Clock className="size-3.5 shrink-0" /> },
  { key: 'escort', label: '任务护航', icon: <ShieldCheck className="size-3.5 shrink-0" /> },
];
export const moreTabs: Array<{ key: MoreConfigTab; label: string; icon: React.ReactNode }> = [
  { key: 'engine', label: '引擎配置', icon: <Settings className="size-3.5 shrink-0" /> },
  { key: 'md', label: 'MD 文档', icon: <FileText className="size-3.5 shrink-0" /> },
  { key: 'node', label: '节点', icon: <Database className="size-3.5 shrink-0" /> },
  { key: 'channel', label: '渠道', icon: <Network className="size-3.5 shrink-0" /> },
  { key: 'approval', label: '发布审批', icon: <ShieldCheck className="size-3.5 shrink-0" /> },
  { key: 'screen', label: '副屏', icon: <Smartphone className="size-3.5 shrink-0" /> },
];
