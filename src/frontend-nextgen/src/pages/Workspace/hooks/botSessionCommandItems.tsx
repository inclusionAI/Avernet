/** 单聊输入框 /命令面板的命令项装配（自 useBotSessionFilesFeature 抽取，行为不变）。 */
import type { CommandItem } from '@tc-chat/ui/es/Sender';
import { Eraser, FileIcon, Zap } from 'lucide-react';

export interface BuildBotSessionCommandItemsParams {
  /** 好友 Bot 不开放 skill 命令。 */
  canUseSkills: boolean;
  skillItems: CommandItem[];
  fileSubItems: CommandItem[];
  skillsLoading: boolean;
  filesLoading: boolean;
  onClear: () => void;
  onSelectSkill: (skillName: string) => void;
}

/** /skill（二级技能面板）+ /file（引用会话文件）+ /clear（清空上下文）三个命令项。 */
export function buildBotSessionCommandItems({
  canUseSkills,
  skillItems,
  fileSubItems,
  skillsLoading,
  filesLoading,
  onClear,
  onSelectSkill,
}: BuildBotSessionCommandItemsParams): CommandItem[] {
  const skillCommand: CommandItem = {
    id: '__skill_entry__',
    name: 'skill',
    description: '唤起技能面板',
    icon: <Zap className="h-3.5 w-3.5 text-muted-foreground" />,
    preventInsert: true,
    subConfig: {
      label: '技能',
      categories: [
        {
          key: '__skill_list__',
          label: '技能',
          icon: <Zap className="h-3.5 w-3.5" />,
          items: skillItems,
          emptyText: skillsLoading ? '加载中…' : '暂无可用技能',
        },
      ],
      onSelect: (item) => {
        if (item.name) onSelectSkill(item.name);
      },
      format: (item) => `/${item.name}`,
    },
  };
  return [
    ...(canUseSkills ? [skillCommand] : []),
    {
      id: '__file_entry__',
      name: 'file',
      description: '引用本会话已上传的文件',
      icon: <FileIcon className="h-3.5 w-3.5 text-muted-foreground" />,
      preventInsert: true,
      subConfig: {
        label: '文件',
        categories: [
          {
            key: '__session_file__',
            label: '文件',
            icon: <FileIcon className="h-3.5 w-3.5" />,
            items: fileSubItems,
            emptyText: filesLoading ? '加载中…' : '暂无可引用文件,请先上传',
          },
        ],
        onSelect: () => {},
        format: () => '',
      },
    },
    {
      id: '__clear__',
      name: 'clear',
      description: '清空当前会话上下文',
      icon: <Eraser className="h-3.5 w-3.5 text-muted-foreground" />,
      preventInsert: true,
      onSelect: () => void onClear(),
    },
  ];
}
