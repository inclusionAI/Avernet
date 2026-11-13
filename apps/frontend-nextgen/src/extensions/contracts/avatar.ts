// Bot 头像编辑器槽位契约（collab-form Task 17）：
// 纯类型文件 —— 运行时零字节；BotAvatar 实现属 BotWorkshop 裁剪面（不进 collab 切片），
// 槽位只描述消费面（值/回调/种子/禁用）。实现文件反向 import 本类型注解（type-only，编译期擦除），
// 签名漂移在 typecheck 阶段失配；本文件不得 import 任何 BotWorkshop / pages 实现代码。
import type { ComponentType, ReactNode } from 'react';

/** Bot 头像编辑器 props（原样搬自 BotAvatar/AvatarEditor.tsx 的 BotAvatarEditorProps）。 */
export interface AvatarEditorProps {
  value: string;
  onChange: (url: string) => void;
  seed: string;
  disabled?: boolean;
  /** 宿主自带上传入口时隐藏基础「上传图片」按钮，避免出现双上传入口。 */
  hideUpload?: boolean;
  /** 追加到动作行的额外按钮（如内部形态的 AI 生成头像），与「换一批/上传图片」同行同规格展示。 */
  extraActions?: ReactNode;
  /** 编辑器宿主浮层（上传/AIGC 等）开合上报；宿主据此抑制外层 Popover/Dialog 的外点关闭连坐。 */
  onOverlayOpenChange?: (open: boolean) => void;
}

/** 头像编辑器组件类型（appExtension.avatarEditor 经 React.lazy 持有的载荷签名）。 */
export type AvatarEditorComponent = ComponentType<AvatarEditorProps>;
