/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * UI Store - UI 状态管理
 */

import type { Skill } from '@/types/assistant';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

type SkillBrowserMode = 'add' | 'browse';

interface UIState {
  // Skill Modal State
  isSkillModalOpen: boolean;
  selectedSkillSetId: string;

  // Sidebar Tab Request（跨组件切换 Sidebar Tab，如从对话框命令面板触发）
  sidebarRequestTab: string | null;
  setSidebarRequestTab: (tab: string | null) => void;

  // Resource Tab 高亮关联链接（从发布弹窗跳转时触发）
  highlightLinkTab: boolean;
  setHighlightLinkTab: (highlight: boolean) => void;

  // Skill Tab 高亮提示（从高敏 MCP 提示跳转时触发）
  highlightSkillTab: boolean;
  setHighlightSkillTab: (highlight: boolean) => void;

  // 高敏 MCP 标签显示控制（从发布弹窗跳转时持续显示，直到页面刷新）
  showHighSensitivityTags: boolean;
  setShowHighSensitivityTags: (show: boolean) => void;

  // Resource Modal State
  isResourceModalOpen: boolean;
  resourceModalTargetPath: string | null;

  // Skill Browser State
  isSkillBrowserOpen: boolean;
  skillBrowserMode: SkillBrowserMode;
  selectedSkillForDetail: Skill | null;
  isSkillDetailDrawerOpen: boolean;

  // Actions
  openSkillModal: (setId: string) => void;
  closeSkillModal: () => void;
  openResourceModal: (targetPath?: string | null) => void;
  closeResourceModal: () => void;
  openSkillBrowser: (mode?: SkillBrowserMode) => void;
  closeSkillBrowser: () => void;
  openSkillDetail: (skill: Skill) => void;
  closeSkillDetail: () => void;
  setSelectedSkillSetId: (id: string) => void;
  reset: () => void;
}

const initialState = {
  isSkillModalOpen: false,
  selectedSkillSetId: '',
  sidebarRequestTab: null as string | null,
  highlightLinkTab: false,
  highlightSkillTab: false,
  showHighSensitivityTags: false,
  isResourceModalOpen: false,
  resourceModalTargetPath: null,
  isSkillBrowserOpen: false,
  skillBrowserMode: 'browse' as SkillBrowserMode,
  selectedSkillForDetail: null,
  isSkillDetailDrawerOpen: false,
};

export const useUIStore = create<UIState>()(
  devtools(
    (set) => ({
      // Initial State
      ...initialState,

      // Actions
      setSidebarRequestTab: (tab) =>
        set({ sidebarRequestTab: tab }, false, 'setSidebarRequestTab'),

      setHighlightLinkTab: (highlight) =>
        set({ highlightLinkTab: highlight }, false, 'setHighlightLinkTab'),

      setHighlightSkillTab: (highlight) =>
        set({ highlightSkillTab: highlight }, false, 'setHighlightSkillTab'),

      setShowHighSensitivityTags: (show) =>
        set(
          { showHighSensitivityTags: show },
          false,
          'setShowHighSensitivityTags',
        ),

      openSkillModal: (setId) =>
        set(
          { isSkillModalOpen: true, selectedSkillSetId: setId },
          false,
          'openSkillModal',
        ),

      closeSkillModal: () =>
        set(
          { isSkillModalOpen: false, selectedSkillSetId: '' },
          false,
          'closeSkillModal',
        ),

      openResourceModal: (targetPath) =>
        set(
          {
            isResourceModalOpen: true,
            resourceModalTargetPath: targetPath ?? null,
          },
          false,
          'openResourceModal',
        ),

      closeResourceModal: () =>
        set({ isResourceModalOpen: false }, false, 'closeResourceModal'),

      openSkillBrowser: (mode = 'browse') =>
        set(
          { isSkillBrowserOpen: true, skillBrowserMode: mode },
          false,
          'openSkillBrowser',
        ),

      closeSkillBrowser: () =>
        set(
          {
            isSkillBrowserOpen: false,
            selectedSkillForDetail: null,
            isSkillDetailDrawerOpen: false,
          },
          false,
          'closeSkillBrowser',
        ),

      openSkillDetail: (skill) =>
        set(
          { selectedSkillForDetail: skill, isSkillDetailDrawerOpen: true },
          false,
          'openSkillDetail',
        ),

      closeSkillDetail: () =>
        set(
          { selectedSkillForDetail: null, isSkillDetailDrawerOpen: false },
          false,
          'closeSkillDetail',
        ),

      setSelectedSkillSetId: (id) =>
        set({ selectedSkillSetId: id }, false, 'setSelectedSkillSetId'),

      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'UIStore' },
  ),
);
