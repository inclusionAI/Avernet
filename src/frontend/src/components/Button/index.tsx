/**
 * Button 组件
 *
 * 基于 CVA 的通用按钮组件，覆盖项目中绝大多数按钮场景。
 *
 * ## 新体系用法（推荐）
 *
 * variant 控制语义意图，soft / ghost 控制填充风格：
 *
 * ```tsx
 * // primary（默认）
 * <Button>提交</Button>
 * <Button soft>编辑</Button>
 *
 * // default — 中性操作
 * <Button variant="default">取消</Button>
 * <Button variant="default" ghost>更多操作</Button>
 *
 * // secondary — 品牌色次级
 * <Button variant="secondary">添加</Button>
 * <Button variant="secondary" soft>切换</Button>
 *
 * // danger — 危险操作
 * <Button variant="danger">删除</Button>
 * <Button variant="danger" soft>移除</Button>
 * <Button variant="danger" ghost>  // hover 才显红
 *
 * // 通用
 * <Button loading>提交中...</Button>
 * <Button fullWidth>确认</Button>
 * <Button size="sm" leftIcon={<Plus size={14} />}>新建</Button>
 * ```
 *
 * ## 旧体系（向后兼容，不推荐新代码使用）
 * ```tsx
 * <Button variant="ghost" size="sm">取消</Button>
 * <Button variant="soft" color="amber" size="sm">重新申请</Button>
 * ```
 */

import React from 'react';
import { buttonVariants, type ButtonVariants } from '@/styles/components';
import { cn } from '@/utils/utils';
import { Loader2 } from 'lucide-react';

const LOADER_SIZE: Record<string, number> = {
  xs: 12,
  sm: 13,
  md: 15,
  lg: 16,
  icon: 16,
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    Omit<ButtonVariants, 'fill'> {
  loading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
  children?: React.ReactNode;
  /** 浅色填充风格（中等视觉权重） */
  soft?: boolean;
  /** 无背景无边框风格（最低视觉权重，hover 才显现） */
  ghost?: boolean;
  /** 灰底 + variant 文字色（卡片内按钮场景） */
  muted?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      soft,
      ghost,
      muted,
      size,
      color,
      fullWidth,
      loading,
      leftIcon,
      rightIcon,
      children,
      disabled,
      ...props
    },
    ref,
  ) => {
    const loaderSize = LOADER_SIZE[size ?? 'md'];
    const fill = ghost ? 'ghost' : soft ? 'soft' : muted ? 'muted' : 'solid';

    return (
      <button
        ref={ref}
        type="button"
        className={cn(
          buttonVariants({ variant, fill, size, color, fullWidth }),
          className,
        )}
        disabled={disabled || loading}
        {...props}
      >
        {loading && <Loader2 className="animate-spin" size={loaderSize} />}
        {!loading && leftIcon}
        {children}
        {!loading && rightIcon}
      </button>
    );
  },
);

Button.displayName = 'Button';

export default Button;
