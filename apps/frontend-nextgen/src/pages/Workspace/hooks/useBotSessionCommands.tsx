/** 普通对话与 Bot 调试共用的懒加载 slash 命令；文件界面由各宿主选择。 */
import type { BotSessionFileView } from '@/services/workspace/botSessionFileService';
import { useBotSessionFileStore } from '@/stores/botSessionFileStore';
import type { CommandConfig, CommandItem } from '@tc-chat/ui/es/Sender';
import { FileIcon, Zap } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { buildBotSessionCommandItems } from './botSessionCommandItems';
import type { UseBotSessionFilesResult } from './useBotSessionFiles';
import type { UseBotSkillsResult } from './useBotSkills';

interface Options {
  enabled: boolean;
  canUseSkills: boolean;
  files: UseBotSessionFilesResult;
  skillsState: UseBotSkillsResult;
  handleReference: (file: BotSessionFileView) => void;
  handleSelectSkill: (name: string) => void;
  onClear: () => void;
}

export function useBotSessionCommands({
  enabled,
  canUseSkills,
  files,
  skillsState,
  handleReference,
  handleSelectSkill,
  onClear,
}: Options): CommandConfig {
  const [fileItems, setFileItems] = useState<CommandItem[]>([]);
  const [skillItems, setSkillItems] = useState<CommandItem[]>([]);
  const generation = useRef(0);
  const refreshFiles = files.refresh;
  // 命令展开才读取当前会话；刷新后取最新 store，避免读取旧闭包。
  const loadFiles = useCallback(async (): Promise<CommandItem[]> => {
    if (!enabled) return [];
    await refreshFiles();
    const { readyFiles } = useBotSessionFileStore.getState();
    return readyFiles.map((f) => ({
      id: `__file__${f.resourceId}`,
      name: f.displayName,
      description: '引用到输入框',
      icon: <FileIcon className="h-3.5 w-3.5 text-muted-foreground" />,
      data: { resource_id: f.resourceId, display_name: f.displayName },
      preventInsert: true,
      onSelect: () => handleReference(f),
    }));
  }, [enabled, refreshFiles, handleReference]);

  const loadSkills = useCallback(async (): Promise<CommandItem[]> => {
    if (!enabled || !canUseSkills) return [];
    const skills = await skillsState.loadSkills();
    return skills.map((s) => ({
      id: `__skill__${s.skillId}`,
      name: s.name,
      description: s.description || (s.active ? '已激活' : '未激活'),
      icon: <Zap className="h-3.5 w-3.5 text-muted-foreground" />,
      data: { skill_id: s.skillId },
    }));
  }, [enabled, canUseSkills, skillsState.loadSkills]);

  useEffect(() => {
    setFileItems([]);
    setSkillItems([]);
    generation.current++;
    return () => {
      generation.current++;
    };
  }, [loadFiles, loadSkills]);

  // SDK 的搜索 effect 仅监听 query/open：从 `/` 进入子菜单时两者都不变，
  // 不会触发 onSearch。入口主动懒加载完整候选，子菜单交给 SDK 本地过滤。
  const openSubmenu = useCallback(
    (item: CommandItem) => {
      const isFile = item.id === '__file_entry__';
      if (!isFile && item.id !== '__skill_entry__') return;
      const request = ++generation.current;
      const update = isFile ? setFileItems : setSkillItems;
      update([]);
      void (isFile ? loadFiles() : loadSkills())
        .then((items) => {
          if (generation.current === request) update(items);
        })
        .catch((error: unknown) => {
          if (generation.current === request) toast.error(error instanceof Error ? error.message : '命令资源加载失败');
        });
    },
    [loadFiles, loadSkills],
  );

  const commandItems = useMemo<CommandItem[]>(
    () =>
      buildBotSessionCommandItems({
        canUseSkills,
        skillItems,
        fileItems,
        skillsLoading: skillsState.isLoading,
        filesLoading: files.isLoadingList,
        onClear,
        onSelectSkill: handleSelectSkill,
      }),
    [canUseSkills, skillItems, fileItems, skillsState.isLoading, files.isLoadingList, onClear, handleSelectSkill],
  );

  const command = useMemo<CommandConfig>(
    () => ({
      categories: [
        {
          key: '__bot_commands__',
          label: '命令',
          items: commandItems,
        },
      ],
      onSelect: openSubmenu,
      format: () => '',
    }),
    [commandItems, openSubmenu],
  );

  return command;
}
