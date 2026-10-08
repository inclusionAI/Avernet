/**
 * 组件级别的样式封装
 * 使用 CVA (Class Variance Authority) 管理组件样式变体
 *
 * 核心思想：将常用的 Tailwind 类名组合抽象成语义化的样式对象
 * 优势：
 * - 类型安全（TypeScript 支持）
 * - 易于维护（集中管理）
 * - 代码简洁（避免长类名串）
 * - 易于复用（多个组件共享）
 */

import { cva, type VariantProps } from 'class-variance-authority';

// ============================================
// 按钮样式变体
// ============================================

/**
 * 按钮样式变体
 *
 * ## 新体系（推荐）
 *
 * 两个维度组合：variant（语义意图）× fill（填充风格）
 *
 * ### variant
 * - `primary`   — 最强调，提交 / 保存 / 确认
 * - `secondary`  — 品牌色次级，编辑 / 添加 / 切换等功能性操作
 * - `default`   — 中性无倾向，取消 / 关闭 / 普通操作
 * - `danger`    — 危险操作，删除 / 重置
 *
 * ### fill
 * - `solid`  — 实心填充（默认），最高视觉权重
 * - `soft`   — 浅色填充，中等视觉权重
 * - `ghost`  — 无背景无边框，最低视觉权重（仅 default / danger 支持）
 *
 * ```tsx
 * <Button>提交</Button>                              // primary solid
 * <Button fill="soft">编辑</Button>                  // primary soft
 * <Button variant="default">取消</Button>            // default solid
 * <Button variant="default" fill="ghost">更多</Button>
 * <Button variant="danger">删除</Button>             // danger solid
 * <Button variant="danger" fill="soft">移除</Button>
 * <Button variant="danger" fill="ghost">           // hover 才显红
 * ```
 *
 * ## 旧体系（向后兼容，新代码不推荐使用）
 * `ghost` / `soft` / `outline` / `link`
 */
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 transition-all focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed',
  {
    variants: {
      variant: {
        // ── 新体系 ──────────────────────────────────────────────────────────
        primary: '',
        secondary: '',
        default: '',
        danger: '',
        success: '',
        // ── 旧体系（向后兼容） ───────────────────────────────────────────────
        ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
        soft: 'border bg-white hover:bg-slate-50',
        outline:
          'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:border-slate-300',
        link: 'text-lavender-600 hover:text-lavender-700 underline-offset-4 hover:underline',
      },
      // fill — 填充风格（配合新体系 variant 使用）
      fill: {
        solid: '',
        soft: '',
        ghost: '',
        muted: '',
      },
      // color — 旧体系 soft+color 组合，向后兼容
      color: {
        default: '',
        lavender: '',
        red: '',
        amber: '',
        blue: '',
        green: '',
        slate: '',
      },
      size: {
        xs: 'h-7 px-2 text-xs rounded-lg',
        sm: 'h-8 px-3 text-xs rounded-lg',
        md: 'h-9 px-4 text-sm rounded-lg',
        lg: 'h-11 px-5 text-sm rounded-lg',
        icon: 'h-9 w-9 rounded-lg',
      },
      fullWidth: {
        true: 'w-full',
        false: '',
      },
    },
    compoundVariants: [
      // ── primary ────────────────────────────────────────────────────────────
      {
        variant: 'primary',
        fill: 'solid',
        className: 'bg-lavender-500 text-white hover:bg-lavender-600',
      },
      {
        variant: 'primary',
        fill: 'soft',
        className: 'bg-lavender-50 text-lavender-600 hover:bg-lavender-100',
      },
      // ── secondary ──────────────────────────────────────────────────────────
      {
        variant: 'secondary',
        fill: 'solid',
        className: 'bg-lavender-100 text-lavender-700 hover:bg-lavender-200',
      },
      {
        variant: 'secondary',
        fill: 'soft',
        className: 'bg-lavender-50 text-lavender-500 hover:bg-lavender-100',
      },
      // ── default ────────────────────────────────────────────────────────────
      {
        variant: 'default',
        fill: 'solid',
        className: 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50 hover:border-slate-300',
      },
      {
        variant: 'default',
        fill: 'soft',
        className: 'bg-slate-50 text-slate-600 hover:bg-slate-100',
      },
      {
        variant: 'default',
        fill: 'ghost',
        className: 'text-slate-600 hover:bg-slate-100',
      },
      // ── success ────────────────────────────────────────────────────────────
      {
        variant: 'success',
        fill: 'solid',
        className: 'bg-emerald-500 text-white hover:bg-emerald-600',
      },
      {
        variant: 'success',
        fill: 'soft',
        className: 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100',
      },
      {
        variant: 'success',
        fill: 'muted',
        className: 'bg-slate-100 text-emerald-600 hover:bg-slate-200',
      },
      // ── muted fill（灰底 + variant 文字色） ────────────────────────────────
      {
        variant: 'primary',
        fill: 'muted',
        className: 'bg-slate-100 text-lavender-600 hover:bg-slate-200',
      },
      {
        variant: 'secondary',
        fill: 'muted',
        className: 'bg-slate-100 text-lavender-500 hover:bg-slate-200',
      },
      {
        variant: 'default',
        fill: 'muted',
        className: 'bg-slate-100 text-slate-600 hover:bg-slate-200',
      },
      {
        variant: 'danger',
        fill: 'muted',
        className: 'bg-slate-100 text-red-600 hover:bg-slate-200',
      },
      // ── danger ─────────────────────────────────────────────────────────────
      {
        variant: 'danger',
        fill: 'solid',
        className: 'bg-red-500 text-white hover:bg-red-600',
      },
      {
        variant: 'danger',
        fill: 'soft',
        className: 'bg-red-50 text-red-600 border border-red-100 hover:bg-red-100',
      },
      {
        variant: 'danger',
        fill: 'ghost',
        className: 'text-slate-400 hover:text-red-500 hover:bg-red-50',
      },
      // ── 旧体系 soft + color（向后兼容） ────────────────────────────────────
      {
        variant: 'soft',
        color: 'lavender',
        className: 'text-lavender-600 border-lavender-200 hover:bg-lavender-50',
      },
      {
        variant: 'soft',
        color: 'red',
        className: 'text-red-600 border-red-200 hover:bg-red-50',
      },
      {
        variant: 'soft',
        color: 'amber',
        className: 'text-amber-600 border-amber-200 hover:bg-amber-50',
      },
      {
        variant: 'soft',
        color: 'blue',
        className: 'text-blue-600 border-blue-200 hover:bg-blue-50',
      },
      {
        variant: 'soft',
        color: 'green',
        className: 'text-green-600 border-green-200 hover:bg-green-50',
      },
      {
        variant: 'soft',
        color: 'slate',
        className: 'text-slate-600 border-slate-200 hover:bg-slate-50',
      },
      {
        variant: 'soft',
        color: 'default',
        className: 'text-lavender-600 border-lavender-200 hover:bg-lavender-50',
      },
    ],
    defaultVariants: {
      variant: 'primary',
      fill: 'solid',
      size: 'md',
      color: 'default',
      fullWidth: false,
    },
  },
);

// TypeScript 类型导出
export type ButtonVariants = VariantProps<typeof buttonVariants>;

// ============================================
// Tab 按钮样式变体
// ============================================

/**
 * Tab 按钮样式
 *
 * 使用示例：
 * ```tsx
 * <button className={tabVariants({ active: isActive })}>
 *   标签页
 * </button>
 * ```
 */
export const tabVariants = cva(
  'flex items-center justify-center gap-1.5 py-2 rounded-lg text-sm font-normal transition-all cursor-pointer whitespace-nowrap',
  {
    variants: {
      active: {
        true: 'bg-white text-slate-700 shadow-sm',
        false: 'text-slate-400 hover:text-slate-600',
      },
      size: {
        sm: 'px-2 py-1.5 text-xs',
        md: 'px-3 py-2 text-sm',
        lg: 'px-4 py-2.5 text-base',
      },
    },
    defaultVariants: {
      active: false,
      size: 'md',
    },
  },
);

export type TabVariants = VariantProps<typeof tabVariants>;

// ============================================
// 图标按钮样式变体
// ============================================

/**
 * 图标按钮（常用于折叠、关闭等）
 *
 * 使用示例：
 * ```tsx
 * <button className={iconButtonVariants({ size: 'sm', variant: 'default' })}>
 *   <ChevronRight size={16} />
 * </button>
 * ```
 */
export const iconButtonVariants = cva(
  'flex items-center justify-center rounded-full transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed',
  {
    variants: {
      variant: {
        default:
          'bg-white border border-lavender-100 text-slate-400 hover:text-lavender-600 hover:border-lavender-300 shadow-sm',
        ghost: 'text-slate-400 hover:text-slate-600 hover:bg-slate-100',
        primary: 'bg-lavender-600 text-white hover:bg-lavender-700',
        danger: 'bg-red-50 text-red-600 hover:bg-red-100',
      },
      size: {
        xs: 'w-5 h-5',
        sm: 'w-6 h-6',
        md: 'w-8 h-8',
        lg: 'w-10 h-10',
        xl: 'w-12 h-12',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'md',
    },
  },
);

export type IconButtonVariants = VariantProps<typeof iconButtonVariants>;

// ============================================
// 卡片样式变体
// ============================================

/**
 * 卡片容器样式
 *
 * 使用示例：
 * ```tsx
 * <div className={cardVariants({ shadow: 'md', hover: true })}>
 *   卡片内容
 * </div>
 * ```
 */
export const cardVariants = cva('bg-white rounded-xl transition-all', {
  variants: {
    bordered: {
      true: 'border border-gray-100',
      false: '',
    },
    shadow: {
      none: '',
      sm: 'shadow-sm',
      md: 'shadow-md',
      lg: 'shadow-lg',
    },
    hover: {
      true: 'hover:shadow-md hover:-translate-y-0.5 cursor-pointer',
      false: '',
    },
    padding: {
      none: '',
      sm: 'p-3',
      md: 'p-4',
      lg: 'p-6',
    },
  },
  defaultVariants: {
    bordered: true,
    shadow: 'sm',
    hover: false,
    padding: 'md',
  },
});

export type CardVariants = VariantProps<typeof cardVariants>;

// ============================================
// 输入框样式变体
// ============================================

/**
 * 输入框样式
 *
 * 使用示例：
 * ```tsx
 * <input className={inputVariants({ size: 'md', variant: 'default' })} />
 * ```
 */
export const inputVariants = cva(
  'w-full px-3 py-2 bg-white border rounded-lg text-sm transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed',
  {
    variants: {
      variant: {
        default:
          'border-gray-200 focus:border-lavender-500 focus:ring-lavender-500',
        error: 'border-red-300 focus:border-red-500 focus:ring-red-500',
        success: 'border-green-300 focus:border-green-500 focus:ring-green-500',
      },
      size: {
        sm: 'h-8 px-2 text-xs',
        md: 'h-10 px-3 text-sm',
        lg: 'h-12 px-4 text-base',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'md',
    },
  },
);

export type InputVariants = VariantProps<typeof inputVariants>;

// ============================================
// Badge / Tag 样式变体
// ============================================

/**
 * Badge 徽章样式
 *
 * 使用示例：
 * ```tsx
 * <span className={badgeVariants({ variant: 'primary' })}>
 *   新消息
 * </span>
 * ```
 */
export const badgeVariants = cva(
  'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium',
  {
    variants: {
      variant: {
        default: 'bg-slate-100 text-slate-700',
        primary: 'bg-lavender-100 text-lavender-700',
        success: 'bg-green-100 text-green-700',
        warning: 'bg-amber-100 text-amber-700',
        danger: 'bg-red-100 text-red-700',
        info: 'bg-blue-100 text-blue-700',
      },
      size: {
        sm: 'px-1.5 py-0.5 text-[10px]',
        md: 'px-2 py-0.5 text-xs',
        lg: 'px-2.5 py-1 text-sm',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'md',
    },
  },
);

export type BadgeVariants = VariantProps<typeof badgeVariants>;

// ============================================
// 模型卡片样式（ConfigPanel 中使用）
// ============================================

/**
 * 模型选择卡片样式
 *
 * 使用示例：
 * ```tsx
 * <button className={modelCardVariants({ active: isSelected })}>
 *   模型信息
 * </button>
 * ```
 */
export const modelCardVariants = cva(
  'flex items-center gap-3 p-3 rounded-xl border transition-all text-left group cursor-pointer',
  {
    variants: {
      active: {
        true: 'bg-lavender-50 border-lavender-200 shadow-sm',
        false:
          'bg-white border-slate-100 hover:border-slate-200 hover:bg-slate-50/50',
      },
    },
    defaultVariants: {
      active: false,
    },
  },
);

export type ModelCardVariants = VariantProps<typeof modelCardVariants>;

// ============================================
// 工具类：常用样式组合
// ============================================

/**
 * 侧边栏容器样式
 */
export const sidebarContainer = {
  base: 'h-full flex flex-col transition-all duration-300 relative',
  collapsed: 'w-14',
  expanded: 'w-56',
} as const;

/**
 * 内容区滚动容器
 */
export const scrollContainer =
  'flex-1 overflow-y-auto custom-scrollbar min-h-0';

/**
 * Section 标题样式
 */
export const sectionTitle =
  'flex items-center gap-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-4';

/**
 * 动画过渡类名
 */
export const transitions = {
  fast: 'transition-all duration-150',
  normal: 'transition-all duration-300',
  slow: 'transition-all duration-500',
} as const;

/**
 * 常用的 Flexbox 布局
 */
export const flexLayouts = {
  center: 'flex items-center justify-center',
  centerVertical: 'flex items-center',
  centerHorizontal: 'flex justify-center',
  between: 'flex items-center justify-between',
  start: 'flex items-start',
  end: 'flex items-end',
  column: 'flex flex-col',
  columnCenter: 'flex flex-col items-center justify-center',
} as const;

/**
 * 常用的位置定位
 */
export const positions = {
  absoluteCenter: 'absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2',
  absoluteTopRight: 'absolute top-0 right-0',
  absoluteTopLeft: 'absolute top-0 left-0',
  absoluteBottomRight: 'absolute bottom-0 right-0',
  absoluteBottomLeft: 'absolute bottom-0 left-0',
} as const;
