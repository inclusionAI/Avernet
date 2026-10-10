// 「加载更多会话」行内扩展口:去边框、居中窄行,chevron 向下暗示「列表继续向下」,
// 呼应二级列表的克制语言;加载中换旋转 loader +「正在加载…」。
// 可访问名沿用既有契约(加载更多会话 / 正在加载…),保持测试与交互行为不变。
import { Button } from '@/components/ui';
import { ChevronDown, LoaderCircle } from 'lucide-react';

export function ConversationLoadMore(props: {
  loading: boolean;
  /** 交互式列表在收藏变更在途时暂停分页。 */
  disabled?: boolean;
  onClick(): void;
}) {
  const { loading, disabled = false, onClick } = props;
  return (
    <div className="flex justify-center px-4 pb-2 pt-1">
      <Button
        variant="ghost"
        size="sm"
        disabled={loading || disabled}
        aria-busy={loading}
        onClick={onClick}
        className="h-7 gap-1 rounded-md font-normal text-muted-foreground hover:text-primary"
      >
        {loading ? (
          <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {loading ? '正在加载…' : '加载更多会话'}
      </Button>
    </div>
  );
}
