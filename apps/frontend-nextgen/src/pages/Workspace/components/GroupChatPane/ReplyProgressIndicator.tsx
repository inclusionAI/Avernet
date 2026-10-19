import { Spin } from '@/components/ui';

/** 群聊消息与处理列表共用的进行中图标。 */
export function ReplyProgressIndicator() {
  return (
    <div className="inline-flex shrink-0" role="img" aria-label="正在回复">
      <Spin className="gap-0 py-0 [&>svg]:size-3.5 [&>svg]:motion-reduce:animate-none" />
    </div>
  );
}
