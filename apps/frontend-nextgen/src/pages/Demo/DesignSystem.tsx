import React, { useState, useCallback } from 'react';
import { toggleDark, isDark } from '@/styles/theme';

// ── SVG 图标组件（lucide 风格，stroke-2 viewBox 24） ──────────

// ── 色板数据 ──
const neutralSwatches = [
  { name: 'primary', light: '240 5.9% 10%', dark: '0 0% 98%' },
  { name: 'secondary', light: '240 4.8% 95.9%', dark: '240 3.7% 15.9%' },
  { name: 'accent', light: '240 4.8% 95.9%', dark: '240 3.7% 15.9%' },
  { name: 'destructive', light: '0 72.2% 50.6%', dark: '0 72.2% 50.6%' },
  { name: 'muted', light: '240 4.8% 95.9%', dark: '240 3.7% 15.9%' },
  { name: 'border', light: '240 5.9% 90%', dark: '240 3.7% 15.9%' },
];
const statusSwatches = [
  { name: 'success', hsl: '142 71% 45%' },
  { name: 'warning', hsl: '38 92% 50%' },
  { name: 'info', hsl: '199 89% 48%' },
  { name: 'brand (靛蓝)', hsl: '243 75% 59%' },
];

const DesignSystem: React.FC = () => {
  const [dark, setDarkState] = useState(isDark());
  const [activePage, setActivePage] = useState('design');
  const [activeTab, setActiveTab] = useState('colors');
  const [collapsibleOpen, setCollapsibleOpen] = useState(true);
  const [accordionOpen, setAccordionOpen] = useState(true);

  const handleToggle = useCallback(() => setDarkState(toggleDark()), []);

  // 按钮样式常量（§3.1）
  const btn = {
    default: 'inline-flex items-center justify-center h-9 px-4 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 transition-opacity',
    secondary: 'inline-flex items-center justify-center h-9 px-4 rounded-md bg-secondary text-secondary-foreground text-xs font-medium hover:opacity-90 transition-opacity',
    outline: 'inline-flex items-center justify-center h-9 px-4 rounded-md border bg-background text-xs font-medium hover:bg-accent transition-colors',
    ghost: 'inline-flex items-center justify-center h-9 px-4 rounded-md hover:bg-accent text-xs font-medium transition-colors',
    destructive: 'inline-flex items-center justify-center h-9 px-4 rounded-md bg-destructive text-destructive-foreground text-xs font-medium hover:opacity-90 transition-opacity',
    link: 'inline-flex items-center justify-center h-9 px-4 rounded-md text-primary text-xs font-medium underline underline-offset-4 hover:opacity-80',
    sm: 'inline-flex items-center justify-center h-8 px-3 rounded-md bg-primary text-primary-foreground text-xs font-medium',
    lg: 'inline-flex items-center justify-center h-10 px-8 rounded-md bg-primary text-primary-foreground text-xs font-medium',
    icon: 'inline-flex items-center justify-center size-9 rounded-md bg-primary text-primary-foreground',
    disabled: 'inline-flex items-center justify-center h-9 px-4 rounded-md bg-primary text-primary-foreground text-xs font-medium opacity-50 pointer-events-none',
  };

  return (
    <div className="h-full overflow-y-auto bg-background text-foreground">
      {/* ════════ 顶部展示栏 h-14(56px) ════════ */}
      <header className="sticky top-0 z-50 h-14 border-b border-border bg-background/95 backdrop-blur flex items-center px-4 gap-3">
        <div className="flex items-center gap-2 font-semibold tracking-tight shrink-0">
          <div className="size-7 rounded-lg bg-brand text-white grid place-items-center text-xs font-bold">tc</div>
          <span className="hidden sm:inline">tc-ui · 设计规范展示</span>
        </div>
        <nav className="flex items-center gap-0.5 ml-2 overflow-x-auto">
          {[
            { id: 'design', label: '设计规范' },
            { id: 'chat', label: '对话协作' },
            { id: 'bot', label: 'Bot 工作台' },
            { id: 'admin', label: '管理后台' },
          ].map((item) => (
            <button type="button" key={item.id} className={`nav-btn ${activePage === item.id ? 'active' : ''}`} onClick={() => setActivePage(item.id)}>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <button type="button" onClick={handleToggle} className="inline-flex items-center justify-center size-8 rounded-md border border-border hover:bg-accent transition-colors text-foreground" aria-label="切换主题">
            <span className="text-xs">{dark ? '☀️' : '🌙'}</span>
          </button>
        </div>
      </header>

      {/* ════════ 信息条 ════════ */}
      <div className="h-12 border-b border-border bg-muted/30 flex items-center px-4 gap-3 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">
          {activePage === 'design' ? '设计规范预览' : activePage === 'chat' ? '对话协作预览' : activePage === 'bot' ? 'Bot 工作台预览' : '管理后台预览'}
        </span>
        <span>·</span>
        <span>v1.2.1 · shadcn/ui + Tailwind CSS v4</span>
      </div>

      <div className="p-4 md:p-6 max-w-screen-2xl mx-auto">

        {/* ════════════════ 设计规范页 ════════════════ */}
        {activePage === 'design' && (
          <div className="space-y-8">

            {/* ── 色彩系统 §2.1 ── */}
            <section className="space-y-3">
              <div>
                <h2 className="text-xl font-semibold tracking-tight">色彩系统</h2>
                <p className="text-xs text-muted-foreground">CSS 变量 + Tailwind v4 @theme inline 映射，暗色模式自动切换</p>
              </div>
              <div className="space-y-4">
                <div>
                  <h3 className="text-xs font-medium mb-3 text-muted-foreground">中性 + 语义</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                    {neutralSwatches.map((s) => (
                      <div key={s.name} className="rounded-lg border border-border bg-card p-3">
                        <div className="swatch h-16 rounded-md mb-2" style={{ background: `hsl(${s.light})` }} />
                        <div className="text-xs font-medium">{s.name}</div>
                        <div className="text-xs text-muted-foreground font-mono">{s.light}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <h3 className="text-xs font-medium mb-3 text-muted-foreground">状态色 + 品牌</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {statusSwatches.map((s) => (
                      <div key={s.name} className="rounded-lg border border-border bg-card p-3">
                        <div className="swatch h-16 rounded-md mb-2" style={{ background: `hsl(${s.hsl})` }} />
                        <div className="text-xs font-medium">{s.name}</div>
                        <div className="text-xs text-muted-foreground font-mono">{s.hsl}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">铁律：颜色只通过 token class 引用（bg-primary、text-destructive 等），禁止裸色值。</p>
            </section>

            {/* ── 字体系统 §2.2 ── */}
            <section className="space-y-3">
              <div>
                <h2 className="text-xl font-semibold tracking-tight pt-2">字体系统</h2>
                <p className="text-xs text-muted-foreground">Inter + HarmonyOS Sans SC，字重 ≤ 600</p>
              </div>
              <div className="rounded-lg border border-border bg-card p-6 space-y-3">
                <div className="text-3xl font-semibold tracking-tight">页面 H1 · 30px semibold</div>
                <div className="text-2xl font-semibold tracking-tight">页面 H2 · 24px semibold</div>
                <div className="text-lg font-semibold">卡片标题 · 18px semibold</div>
                <div className="text-xs">正文 · 12px normal —— text-xs 是全站正文默认字号。</div>
                <div className="text-xs text-muted-foreground">次要说明 · 12px muted-foreground</div>
                <div className="text-xs text-muted-foreground uppercase tracking-wider">表头/标签 · 12px uppercase</div>
                <div className="text-[10px] text-muted-foreground">Badge / pill · 10px</div>
                <div className="text-2xl font-semibold tabular-nums">¥ 12,345.67 · <span className="text-xs font-normal text-success">+12.5%</span></div>
                <div className="font-mono text-xs bg-muted px-2 py-1 rounded inline-block">const x = await fetch()</div>
              </div>
            </section>

            {/* ── 圆角阴影间距 §2.4 §2.5 §2.3 ── */}
            <section className="space-y-3">
              <div>
                <h2 className="text-xl font-semibold tracking-tight pt-2">圆角 · 阴影 · 间距</h2>
                <p className="text-xs text-muted-foreground">默认 rounded-lg(8px)，对话输入区例外 rounded-2xl(16px)</p>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                <div className="rounded-sm border border-border bg-card h-20 grid place-items-center text-xs text-muted-foreground">sm · 4px</div>
                <div className="rounded-md border border-border bg-card h-20 grid place-items-center text-xs text-muted-foreground">md · 6px</div>
                <div className="rounded-lg border border-border bg-card shadow-sm h-20 grid place-items-center text-xs text-muted-foreground">lg · 8px · 默认</div>
                <div className="rounded-xl border border-border bg-card h-20 grid place-items-center text-xs text-muted-foreground">xl · 12px</div>
                <div className="rounded-2xl border border-border bg-card h-20 grid place-items-center text-xs text-muted-foreground">2xl · 16px · 对话输入区</div>
                <div className="rounded-full border border-border bg-card h-20 grid place-items-center text-xs text-muted-foreground">full · 胶囊</div>
              </div>
            </section>

            {/* ════════════ 组件规范 §3 ════════════ */}
            <section className="space-y-8">
              <div>
                <h2 className="text-xl font-semibold tracking-tight">组件规范</h2>
                <p className="text-xs text-muted-foreground">基于 shadcn/ui，未覆盖项走官方默认</p>
              </div>

              {/* Button §3.1 */}
              <div className="rounded-lg border border-border bg-card p-6 space-y-4">
                <h3 className="text-xs font-medium text-muted-foreground">Button</h3>
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" className={btn.default}>Primary 主操作</button>
                  <button type="button" className={btn.secondary}>Secondary 次操作</button>
                  <button type="button" className={btn.outline}>Outline 线框</button>
                  <button type="button" className={btn.ghost}>Ghost 幽灵</button>
                  <button type="button" className={btn.destructive}>Destructive 危险</button>
                  <button type="button" className={btn.link}>Link 链接</button>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <button type="button" className={btn.sm}>尺寸 sm</button>
                  <button type="button" className={btn.default}>尺寸 default</button>
                  <button type="button" className={btn.lg}>尺寸 lg</button>
                  <button type="button" className={btn.icon} aria-label="图标按钮">
                    <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 5v14M5 12h14" /></svg>
                  </button>
                  <button type="button" disabled className={btn.disabled}>Disabled</button>
                </div>
                <p className="text-xs text-muted-foreground">铁律：一个操作区最多 1 个 primary 主按钮，其余 outline/ghost。按钮 hover 用 opacity-90 而非位移或变色。</p>
              </div>

              {/* Input §3.2 */}
              <div className="rounded-lg border border-border bg-card p-6 space-y-4">
                <h3 className="text-xs font-medium text-muted-foreground">Input / Select / Textarea</h3>
                <div className="grid md:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">项目名称</label>
                    <input type="text" defaultValue="tc-ui-02" className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" />
                    <p className="text-xs text-muted-foreground">辅助说明文字</p>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">选择模型</label>
                    <select className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs"><option>GPT-4o</option><option>Claude</option></select>
                  </div>
                  <div className="space-y-1.5 md:col-span-2">
                    <label className="text-xs font-medium">描述</label>
                    <textarea className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-xs min-h-[80px]">输入框高度统一 36px，focus 时 2px ring。</textarea>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium">错误示例</label>
                    <input type="text" defaultValue="无效值" className="flex h-9 w-full rounded-md border border-destructive bg-background px-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive" />
                    <p className="text-xs text-destructive">该字段不能为空</p>
                  </div>
                </div>
              </div>

              {/* Card 统计卡片 §4.3 */}
              <div className="grid md:grid-cols-3 gap-4">
                <div className="rounded-lg border border-border bg-card text-card-foreground shadow-sm p-6">
                  <div className="space-y-1.5">
                    <div className="text-xs text-muted-foreground">月活用户</div>
                    <div className="text-2xl font-semibold tabular-nums">128,402</div>
                    <div className="flex items-center gap-1 text-xs"><span className="inline-flex items-center rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-success">+12.5%</span><span className="text-muted-foreground">vs 上月</span></div>
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-card text-card-foreground shadow-sm p-6">
                  <div className="space-y-1.5">
                    <div className="text-xs text-muted-foreground">API 调用</div>
                    <div className="text-2xl font-semibold tabular-nums">2.4M</div>
                    <div className="flex items-center gap-1 text-xs"><span className="inline-flex items-center rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-success">+8.2%</span><span className="text-muted-foreground">vs 上周</span></div>
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-card text-card-foreground shadow-sm p-6">
                  <div className="space-y-1.5">
                    <div className="text-xs text-muted-foreground">错误率</div>
                    <div className="text-2xl font-semibold tabular-nums">0.03%</div>
                    <div className="flex items-center gap-1 text-xs"><span className="inline-flex items-center rounded-full border border-destructive/30 bg-destructive/10 px-2 py-0.5 text-destructive">-0.01%</span><span className="text-muted-foreground">vs 昨日</span></div>
                  </div>
                </div>
              </div>

              {/* Badge / Tag */}
              <div className="rounded-lg border border-border bg-card p-6 space-y-4">
                <h3 className="text-xs font-medium text-muted-foreground">Badge / Tag</h3>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-[10px] font-medium text-success">运行中</span>
                  <span className="inline-flex items-center rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[10px] font-medium text-warning">待审核</span>
                  <span className="inline-flex items-center rounded-full border border-destructive/30 bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive">已停止</span>
                  <span className="inline-flex items-center rounded-full border border-info/30 bg-info/10 px-2 py-0.5 text-[10px] font-medium text-info">同步中</span>
                  <span className="inline-flex items-center rounded-full bg-secondary text-secondary-foreground px-2 py-0.5 text-[10px] font-medium">团队</span>
                  <span className="inline-flex items-center rounded-full bg-secondary text-secondary-foreground px-2 py-0.5 text-[10px] font-medium">个人</span>
                  <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                    <svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>
                    OpenClaw
                  </span>
                </div>
              </div>

              {/* Tabs §3.x */}
              <div className="rounded-lg border border-border bg-card p-6 space-y-4">
                <h3 className="text-xs font-medium text-muted-foreground">Tabs / Segmented</h3>
                <div className="flex items-center gap-1 border-b border-border">
                  {[
                    { id: 'colors', label: '色彩' },
                    { id: 'fonts', label: '字体' },
                    { id: 'components', label: '组件' },
                  ].map((t) => (
                    <div key={t.id} className={`tab-item ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>{t.label}</div>
                  ))}
                </div>
                <div className="flex items-center gap-1">
                  <button type="button" className="px-3 h-8 rounded-md text-xs font-medium bg-secondary text-secondary-foreground">代码</button>
                  <button type="button" className="px-3 h-8 rounded-md text-xs text-muted-foreground hover:bg-accent transition-colors">预览</button>
                  <button type="button" className="px-3 h-8 rounded-md text-xs text-muted-foreground hover:bg-accent transition-colors">文档</button>
                </div>
              </div>

              {/* Skeleton + Progress */}
              <div className="grid md:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-3">Skeleton 骨架屏</h3>
                  <div className="space-y-3">
                    <div className="flex items-center gap-3"><div className="size-10 rounded-full bg-muted animate-pulse" /><div className="flex-1 space-y-2"><div className="h-4 w-1/3 rounded bg-muted animate-pulse" /><div className="h-3 w-1/2 rounded bg-muted animate-pulse" /></div></div>
                    <div className="h-4 w-full rounded bg-muted animate-pulse" />
                    <div className="h-4 w-5/6 rounded bg-muted animate-pulse" />
                    <div className="h-4 w-3/4 rounded bg-muted animate-pulse" />
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-4">Progress 进度条</h3>
                  <div className="space-y-4">
                    <div>
                      <div className="flex justify-between text-xs mb-1"><span>上传文件</span><span className="text-muted-foreground tabular-nums">42%</span></div>
                      <div className="h-2 rounded-full bg-muted overflow-hidden"><div className="h-full bg-primary transition-all duration-300" style={{ width: '42%' }} /></div>
                    </div>
                    <div>
                      <div className="flex justify-between text-xs mb-1"><span>磁盘空间</span><span className="text-warning tabular-nums">84%</span></div>
                      <div className="h-2 rounded-full bg-muted overflow-hidden"><div className="h-full bg-warning transition-all duration-300" style={{ width: '84%' }} /></div>
                    </div>
                    <div>
                      <div className="flex justify-between text-xs mb-1"><span>内存</span><span className="text-destructive tabular-nums">97%</span></div>
                      <div className="h-2 rounded-full bg-muted overflow-hidden"><div className="h-full bg-destructive transition-all duration-300" style={{ width: '97%' }} /></div>
                    </div>
                    <div className="flex items-center gap-3 pt-2">
                      <div className="relative size-10">
                        <svg className="size-10 -rotate-90" viewBox="0 0 36 36"><circle cx="18" cy="18" r="16" fill="none" stroke="hsl(var(--muted))" strokeWidth="3" /><circle cx="18" cy="18" r="16" fill="none" stroke="hsl(var(--primary))" strokeWidth="3" strokeDasharray="100" strokeDashoffset="65" strokeLinecap="round" /></svg>
                        <div className="absolute inset-0 grid place-items-center text-xs font-medium">35%</div>
                      </div>
                      <span className="text-xs text-muted-foreground">环形进度</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Avatar + Accordion */}
              <div className="grid md:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-3">Avatar</h3>
                  <div className="flex items-center gap-6">
                    <div className="flex -space-x-2">
                      <div className="size-8 rounded-full bg-brand/15 grid place-items-center text-[10px] font-medium text-brand ring-2 ring-background">A</div>
                      <div className="size-8 rounded-full bg-success/15 grid place-items-center text-[10px] font-medium text-success ring-2 ring-background">B</div>
                      <div className="size-8 rounded-full bg-warning/15 grid place-items-center text-[10px] font-medium text-warning ring-2 ring-background">C</div>
                      <div className="size-8 rounded-full bg-muted grid place-items-center text-[10px] text-muted-foreground ring-2 ring-background">+5</div>
                    </div>
                    <div className="relative"><div className="size-10 rounded-full bg-brand/15 grid place-items-center text-[10px] font-medium text-brand">C</div><div className="absolute bottom-0 right-0 size-2.5 rounded-full bg-success ring-2 ring-background" /></div>
                    <div className="size-6 rounded-full bg-muted grid place-items-center text-[10px] text-muted-foreground">S</div>
                    <div className="size-12 rounded-full bg-info/15 grid place-items-center text-[10px] font-medium text-info">XL</div>
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-3">Accordion</h3>
                  <div className="border-b border-border">
                    <div className="flex items-center py-3 px-2 text-xs font-medium hover:bg-accent/50 cursor-pointer" onClick={() => setAccordionOpen(!accordionOpen)}>
                      什么是 vibecoding？
                      <svg className={`size-4 ml-auto text-muted-foreground transition-transform ${accordionOpen ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>
                    </div>
                    {accordionOpen && <div className="pb-3 px-2 text-xs text-muted-foreground">用自然语言描述需求，AI 生成代码，人工微调。</div>}
                  </div>
                  <div className="border-b border-border">
                    <div className="flex items-center py-3 px-2 text-xs font-medium hover:bg-accent/50 cursor-pointer">如何接入规范？<svg className="size-4 ml-auto text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg></div>
                  </div>
                  <div><div className="flex items-center py-3 px-2 text-xs font-medium hover:bg-accent/50 cursor-pointer opacity-50">已禁用项<svg className="size-4 ml-auto text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg></div></div>
                </div>
              </div>

              {/* Pagination + Steps §3.29 */}
              <div className="grid md:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-4">Pagination</h3>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">共 128 条 · 第 1-20 条</span>
                    <div className="flex items-center gap-1">
                      <button type="button" className="size-9 rounded-md border border-border bg-background grid place-items-center hover:bg-accent text-muted-foreground"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m15 18-6-6 6-6" /></svg></button>
                      <button type="button" className="size-9 rounded-md bg-primary text-primary-foreground text-xs">1</button>
                      <button type="button" className="size-9 rounded-md hover:bg-accent text-xs">2</button>
                      <button type="button" className="size-9 rounded-md hover:bg-accent text-xs">3</button>
                      <span className="text-muted-foreground text-xs px-1">…</span>
                      <button type="button" className="size-9 rounded-md hover:bg-accent text-xs">7</button>
                      <button type="button" className="size-9 rounded-md border border-border bg-background grid place-items-center hover:bg-accent"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 18 6-6-6-6" /></svg></button>
                    </div>
                  </div>
                </div>
                <div className="rounded-lg border border-border bg-card p-6">
                  <h3 className="text-xs font-medium text-muted-foreground mb-4">Steps / Wizard</h3>
                  <div className="flex items-center">
                    <div className="flex flex-col items-center"><div className="size-9 rounded-full bg-primary text-primary-foreground border-2 border-primary grid place-items-center"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6 9 17l-5-5" /></svg></div><span className="text-xs font-medium mt-2">账号</span></div>
                    <div className="h-px flex-1 bg-primary mx-2 -mt-6" />
                    <div className="flex flex-col items-center"><div className="size-9 rounded-full bg-primary text-primary-foreground border-2 border-primary grid place-items-center text-[10px] font-medium">2</div><span className="text-xs font-medium mt-2">配置</span></div>
                    <div className="h-px flex-1 bg-border mx-2 -mt-6" />
                    <div className="flex flex-col items-center"><div className="size-9 rounded-full border-2 border-muted-foreground/30 text-muted-foreground grid place-items-center text-[10px]">3</div><span className="text-xs text-muted-foreground mt-2">确认</span></div>
                    <div className="h-px flex-1 bg-border mx-2 -mt-6" />
                    <div className="flex flex-col items-center"><div className="size-9 rounded-full border-2 border-muted-foreground/30 text-muted-foreground grid place-items-center text-[10px]">4</div><span className="text-xs text-muted-foreground mt-2">完成</span></div>
                  </div>
                </div>
              </div>


            {/* Empty / Error §3.10 §5.13 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">Empty / Error 状态</div>
              <div className="grid md:grid-cols-2">
                <div className="text-center py-12 px-4 border-r border-border">
                  <div className="size-12 mx-auto rounded-full bg-brand/10 grid place-items-center text-brand"><svg className="size-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" /></svg></div>
                  <h3 className="text-base font-semibold mt-4">暂无数据</h3>
                  <p className="text-xs text-muted-foreground mt-1">还没有创建任何 Bot</p>
                  <button type="button" className={btn.default + ' mt-4'}>创建第一个 Bot</button>
                </div>
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 m-4">
                  <div className="flex items-center gap-2"><svg className="size-5 text-destructive" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 9v4M12 17h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /></svg><span className="text-xs">同步失败</span></div>
                  <div className="text-xs text-muted-foreground mt-0.5">数据库连接超时，请检查网络后重试。</div>
                  <button type="button" className="mt-3 inline-flex items-center h-8 px-3 rounded-md border border-border bg-background text-xs hover:bg-accent">重试</button>
                </div>
              </div>
            </div>

            {/* 表单控件 §3.x */}
            <div className="grid md:grid-cols-3 gap-4">
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Checkbox / Radio / Switch</h3>
                <div className="space-y-3">
                  <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="checkbox" defaultChecked className="size-4 rounded border-input accent-primary" />启用缓存</label>
                  <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="checkbox" className="size-4 rounded border-input accent-primary" />自动重试</label>
                  <div className="space-y-2 pt-2">
                    <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="radio" name="r1" defaultChecked className="accent-primary" />GPT-4o</label>
                    <label className="flex items-center gap-2 text-xs cursor-pointer"><input type="radio" name="r1" className="accent-primary" />Claude 3.5</label>
                  </div>
                  <div className="flex items-center justify-between pt-2">
                    <span className="text-xs">推送通知</span>
                    <button type="button" className="relative h-5 w-9 rounded-full bg-primary transition-colors duration-150"><span className="absolute top-0.5 size-4 rounded-full bg-white translate-x-4 transition-transform duration-150" /></button>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs">自动更新</span>
                    <button type="button" className="relative h-5 w-9 rounded-full bg-muted transition-colors duration-150"><span className="absolute top-0.5 size-4 rounded-full bg-white translate-x-0.5 transition-transform duration-150" /></button>
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Select / Date / Slider</h3>
                <div className="space-y-3">
                  <select className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs"><option>线上环境</option><option>预发环境</option></select>
                  <input type="date" className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs" />
                  <div>
                    <div className="flex justify-between text-xs mb-1"><span>温度</span><span className="text-muted-foreground tabular-nums">0.3</span></div>
                    <input type="range" className="w-full accent-brand" min="0" max="2" step="0.1" defaultValue="0.3" />
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Dropdown Menu</h3>
                <div className="w-48 rounded-lg border border-border bg-popover p-1 shadow-md">
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>编辑</button>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>查看</button>
                  <div className="h-px bg-border my-1" />
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-destructive/10 text-destructive text-xs"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>删除</button>
                </div>
              </div>
            </div>

            {/* Alert / Callout */}
            <div className="rounded-lg border border-border bg-card p-6 space-y-3">
              <h3 className="text-xs font-medium text-muted-foreground">Alert / Callout</h3>
              <div className="rounded-lg border border-info/30 bg-info/10 p-4 flex items-start gap-3"><svg className="size-5 text-info mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg><div><div className="text-xs font-medium">信息提示</div><div className="text-xs text-muted-foreground mt-0.5">系统将于今晚 22:00 进行维护。</div></div></div>
              <div className="rounded-lg border border-success/30 bg-success/10 p-4 flex items-start gap-3"><svg className="size-5 text-success mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 6 9 17l-5-5" /></svg><div><div className="text-xs font-medium">操作成功</div><div className="text-xs text-muted-foreground mt-0.5">Bot 已成功部署到线上环境。</div></div></div>
              <div className="rounded-lg border border-warning/30 bg-warning/10 p-4 flex items-start gap-3"><svg className="size-5 text-warning mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /><path d="M12 9v4M12 17h.01" /></svg><div><div className="text-xs font-medium">容量警告</div><div className="text-xs text-muted-foreground mt-0.5">上下文用量已达 84%，接近上限。</div></div></div>
              <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 flex items-start gap-3"><svg className="size-5 text-destructive mt-0.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /><path d="M12 9v4M12 17h.01" /></svg><div><div className="text-xs font-medium">同步失败</div><div className="text-xs text-muted-foreground mt-0.5">数据库连接超时，请检查网络后重试。</div></div></div>
            </div>

            {/* Transfer + Timeline */}
            <div className="grid md:grid-cols-2 gap-4">
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Transfer 穿梭框</h3>
                <div className="flex items-center gap-2">
                  <div className="w-40 rounded-md border border-border">
                    <div className="px-2 py-1.5 border-b border-border text-xs text-muted-foreground flex items-center justify-between">可选权限 <span>4</span></div>
                    <div className="max-h-36 overflow-y-auto">
                      <div className="px-2 py-1.5 text-xs hover:bg-accent cursor-pointer rounded-sm m-0.5">读取用户</div>
                      <div className="px-2 py-1.5 text-xs hover:bg-accent cursor-pointer rounded-sm m-0.5">写入配置</div>
                      <div className="px-2 py-1.5 text-xs hover:bg-accent cursor-pointer rounded-sm m-0.5">部署管理</div>
                      <div className="px-2 py-1.5 text-xs hover:bg-accent cursor-pointer rounded-sm m-0.5">查看日志</div>
                    </div>
                  </div>
                  <div className="flex flex-col gap-2">
                    <button type="button" className="size-8 rounded-md border border-border bg-background grid place-items-center hover:bg-accent"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m18 16 4-4-4-4M6 8l-4 4 4 4M14 4l4 0M14 20l4 0M14 12l4 0" /></svg></button>
                    <button type="button" className="size-8 rounded-md border border-border bg-background grid place-items-center hover:bg-accent opacity-40"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 8-4 4 4 4M18 16l4-4-4-4M10 4l-4 0M10 20l-4 0M10 12l-4 0" /></svg></button>
                  </div>
                  <div className="w-40 rounded-md border border-border">
                    <div className="px-2 py-1.5 border-b border-border text-xs text-muted-foreground flex items-center justify-between">已选 <span>1</span></div>
                    <div className="max-h-36 overflow-y-auto">
                      <div className="px-2 py-1.5 text-xs bg-secondary m-0.5 rounded-sm">读取用户</div>
                    </div>
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Timeline 时间线</h3>
                <div className="relative pl-6 space-y-4">
                  <div className="relative"><div className="absolute -left-6 top-0.5 size-3 rounded-full bg-primary ring-4 ring-primary/20" /><div className="text-xs font-medium">部署完成</div><div className="text-xs text-muted-foreground">线上环境 · 5分钟前</div></div>
                  <div className="relative"><div className="absolute -left-6 top-0.5 size-3 rounded-full bg-success ring-4 ring-success/20" /><div className="text-xs font-medium">健康检查通过</div><div className="text-xs text-muted-foreground">自动检查 · 8分钟前</div></div>
                  <div className="relative"><div className="absolute -left-6 top-0.5 size-3 rounded-full bg-muted-foreground/40 ring-4 ring-muted-foreground/10" /><div className="text-xs font-medium">开始部署</div><div className="text-xs text-muted-foreground">手动触发 · 10分钟前</div></div>
                  <div className="relative"><div className="absolute -left-6 top-0.5 size-3 rounded-full bg-muted-foreground/40 ring-4 ring-muted-foreground/10" /><div className="text-xs font-medium">代码合并</div><div className="text-xs text-muted-foreground">PR #42 · 1小时前</div></div>
                </div>
              </div>
            </div>

            {/* Popover + Notification + UserMenu */}
            <div className="grid md:grid-cols-3 gap-4">
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">Popover</h3>
                <div className="w-56 rounded-lg border border-border bg-popover p-3 shadow-md">
                  <div className="text-xs font-medium mb-1">确认删除</div>
                  <div className="text-xs text-muted-foreground mb-3">删除后不可恢复，确定继续？</div>
                  <div className="flex items-center gap-2 justify-end">
                    <button type="button" className="h-8 px-3 rounded-md text-xs hover:bg-accent">取消</button>
                    <button type="button" className="h-8 px-3 rounded-md bg-destructive text-destructive-foreground text-xs hover:opacity-90">删除</button>
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">Notification 下拉</h3>
                <div className="w-72 max-h-[300px] overflow-y-auto">
                  <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border"><button type="button" className="px-2 h-7 rounded-md text-xs font-medium bg-secondary">全部</button><button type="button" className="px-2 h-7 rounded-md text-xs text-muted-foreground hover:bg-accent">未读</button></div>
                  <div className="relative px-3 py-2.5 hover:bg-accent cursor-pointer border-l-2 border-brand">
                    <div className="text-xs font-medium">部署成功</div>
                    <div className="text-xs text-muted-foreground">代码审查 Bot 已上线</div>
                    <div className="text-xs text-muted-foreground mt-0.5">5分钟前</div>
                  </div>
                  <div className="relative px-3 py-2.5 hover:bg-accent cursor-pointer border-l-2 border-transparent">
                    <div className="text-xs font-medium">新评论</div>
                    <div className="text-xs text-muted-foreground">张三评论了你的 PR</div>
                    <div className="text-xs text-muted-foreground mt-0.5">1小时前</div>
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">UserMenu</h3>
                <div className="w-56 rounded-lg border border-border bg-popover p-1 shadow-md">
                  <div className="px-2 py-2 border-b border-border mb-1">
                    <div className="text-xs font-medium">张三</div>
                    <div className="text-xs text-muted-foreground truncate">zhangsan@teamclaw.cn</div>
                  </div>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs">个人资料</button>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs">设置</button>
                  <div className="h-px bg-border my-1" />
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-destructive/10 text-destructive text-xs">退出登录</button>
                </div>
              </div>
            </div>

            {/* Tree + Cascader */}
            <div className="grid md:grid-cols-2 gap-4">
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Tree 树形</h3>
                <div className="space-y-0.5 text-xs">
                  <div className="flex items-center gap-1.5 py-1 px-2 hover:bg-accent rounded-sm cursor-pointer"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m7 15 5 5 5-5M7 9l5-5 5 5" /></svg><span className="font-medium">src</span></div>
                  <div className="flex items-center gap-1.5 py-1 px-2 pl-6 hover:bg-accent rounded-sm cursor-pointer"><div className="size-4" /><input type="checkbox" defaultChecked className="size-4 rounded accent-primary" /><span>components</span></div>
                  <div className="flex items-center gap-1.5 py-1 px-2 pl-6 hover:bg-accent rounded-sm cursor-pointer"><div className="size-4" /><input type="checkbox" className="size-4 rounded accent-primary" /><span>pages</span></div>
                  <div className="flex items-center gap-1.5 py-1 px-2 pl-10 hover:bg-accent rounded-sm cursor-pointer bg-secondary"><div className="size-4" /><input type="checkbox" defaultChecked className="size-4 rounded accent-primary" /><span>Dashboard</span></div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-4">Cascader 级联</h3>
                <div className="flex rounded-md border border-border overflow-hidden">
                  <div className="w-32 border-r border-border p-1">
                    <div className="px-2 py-1.5 rounded-sm text-xs bg-accent font-medium cursor-pointer">中国</div>
                    <div className="px-2 py-1.5 rounded-sm text-xs hover:bg-accent cursor-pointer">美国</div>
                  </div>
                  <div className="w-32 border-r border-border p-1">
                    <div className="px-2 py-1.5 rounded-sm text-xs bg-accent font-medium cursor-pointer">浙江省</div>
                    <div className="px-2 py-1.5 rounded-sm text-xs hover:bg-accent cursor-pointer">广东省</div>
                  </div>
                  <div className="w-32 p-1">
                    <div className="px-2 py-1.5 rounded-sm text-xs bg-accent font-medium cursor-pointer">杭州市</div>
                    <div className="px-2 py-1.5 rounded-sm text-xs hover:bg-accent cursor-pointer">宁波市</div>
                  </div>
                </div>
                <div className="text-xs text-muted-foreground mt-3">中国 / 浙江省 / 杭州市</div>
              </div>
            </div>

            {/* Master-Detail + Table */}
            <div className="grid md:grid-cols-2 gap-4">
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">Master-Detail 主从布局</div>
                <div className="flex h-[280px]">
                  <div className="w-48 border-r border-border shrink-0 flex flex-col">
                    <div className="p-2 border-b border-border"><div className="relative"><svg className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg><input placeholder="搜索…" className="w-full h-8 pl-8 pr-2 rounded-md border border-border bg-background text-xs" /></div></div>
                    <div className="flex-1 overflow-y-auto">
                      <a className="block px-3 py-2.5 border-l-2 border-primary bg-secondary cursor-pointer"><div className="text-xs font-medium truncate">重构登录流程</div><div className="text-xs text-muted-foreground">进行中 · 75%</div></a>
                      <a className="block px-3 py-2.5 border-l-2 border-transparent hover:bg-accent cursor-pointer"><div className="text-xs font-medium truncate">生成 Mock 数据</div><div className="text-xs text-muted-foreground">已完成 · 100%</div></a>
                      <a className="block px-3 py-2.5 border-l-2 border-transparent hover:bg-accent cursor-pointer"><div className="text-xs font-medium truncate">排查 404</div><div className="text-xs text-muted-foreground">待开始 · 0%</div></a>
                    </div>
                  </div>
                  <div className="flex-1 min-w-0 p-4">
                    <div className="text-xs font-medium mb-2">重构登录流程</div>
                    <div className="space-y-2">
                      <div><span className="text-xs text-muted-foreground">负责人</span><div className="text-xs mt-0.5">张三</div></div>
                      <div><span className="text-xs text-muted-foreground">优先级</span><div className="text-xs mt-0.5">高</div></div>
                      <div><span className="text-xs text-muted-foreground">进度</span><div className="h-2 rounded-full bg-muted overflow-hidden mt-1"><div className="h-full bg-primary" style={{ width: '75%' }} /></div></div>
                    </div>
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">Table 表格</div>
                <table className="w-full">
                  <thead><tr className="h-10 text-xs font-medium text-muted-foreground uppercase tracking-wider border-b border-border"><th className="text-left px-4">名称</th><th className="text-left px-4">状态</th><th className="text-right px-4">进度</th></tr></thead>
                  <tbody>
                    {[
                      { name: '重构登录流程', status: '进行中', statusColor: 'warning', progress: 75 },
                      { name: '生成 Mock 数据', status: '已完成', statusColor: 'success', progress: 100 },
                      { name: '排查 404', status: '待开始', statusColor: 'muted', progress: 0 },
                    ].map((row) => (
                      <tr key={row.name} className="h-10 hover:bg-muted/50 transition-colors border-b border-border last:border-0">
                        <td className="px-4 text-xs">{row.name}</td>
                        <td className="px-4"><span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${row.statusColor === 'success' ? 'border-success/30 bg-success/10 text-success' : row.statusColor === 'warning' ? 'border-warning/30 bg-warning/10 text-warning' : 'border-border bg-secondary text-muted-foreground'}`}>{row.status}</span></td>
                        <td className="px-4 text-right text-xs tabular-nums">{row.progress}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* PageHeader + FilterBar + BulkActionBar */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">PageHeader + FilterBar + BulkActionBar</div>
              <div className="p-6 space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1"><h3 className="text-2xl font-semibold tracking-tight">Bot 管理</h3><p className="text-xs text-muted-foreground">管理所有 AI Bot</p></div>
                  <div className="flex items-center gap-2"><button type="button" className={btn.outline}>导入</button><button type="button" className={btn.default}>创建 Bot</button></div>
                </div>
                <div className="flex flex-wrap items-center gap-2 py-3 border-t border-border">
                  <div className="relative w-64"><svg className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg><input placeholder="搜索…" className="w-full h-9 pl-8 pr-2 rounded-md border border-border bg-background text-xs" /></div>
                  <select className="h-9 rounded-md border border-border bg-background px-3 text-xs min-w-[140px]"><option>全部状态</option><option>运行中</option></select>
                  <button type="button" className="ml-auto h-9 px-3 rounded-md text-xs text-muted-foreground hover:bg-accent">重置</button>
                  <span className="text-xs text-muted-foreground">共 24 条</span>
                </div>
                <div className="h-14 bg-card border-b border-border shadow flex items-center px-4 gap-3">
                  <span className="text-xs font-medium">已选 3 项</span>
                  <button type="button" className="text-xs text-muted-foreground hover:text-foreground underline">取消选择</button>
                  <div className="h-4 w-px bg-border mx-1" />
                  <button type="button" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-destructive/30 text-destructive text-xs hover:bg-destructive/10"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>批量删除</button>
                  <button type="button" className="inline-flex items-center h-8 px-3 rounded-md border border-border bg-background text-xs hover:bg-accent">批量导出</button>
                </div>
              </div>
            </div>
            </section>
          </div>

        )}
        {/* ════════════════ 对话交互 §5 全览（设计规范页内嵌） ════════════════ */}
        {activePage === 'design' && (
          <div className="space-y-8 mt-8">

            {/* §4. 对话交互标题 */}
            <section className="space-y-3">
              <div>
                <h2 className="text-xl font-semibold tracking-tight">对话交互（18 小节全览）</h2>
                <p className="text-xs text-muted-foreground">统一左对齐 · 三栏布局 · 参考 ChatGPT / NextChat / LibreChat</p>
              </div>
            </section>

            {/* 5.12 空状态 + 建议提示词 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">5.12 空状态与引导</div>
              <div className="text-center py-16">
                <h3 className="text-3xl font-semibold tracking-tight">有什么可以帮你？</h3>
                <p className="text-xs text-muted-foreground mt-2">选一个起点，或直接在下面输入</p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl mx-auto mt-8">
                  {[
                    { icon: 'M16 18 6 8 2 12l4 6', title: '写一段代码', desc: '描述需求，直接生成可运行代码' },
                    { icon: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6', title: '总结这份文档', desc: '上传文件，自动提炼要点' },
                    { icon: 'm12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2Z', title: '排查报错', desc: '分析日志并给出修复补丁' },
                    { icon: 'M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20', title: '翻译文档', desc: '保持术语一致性' },
                  ].map((card) => (
                    <button type="button" key={card.title} className="rounded-lg border border-border bg-card p-4 hover:shadow hover:border-brand/40 transition-all duration-200 cursor-pointer text-left">
                      <div className="flex items-center gap-2"><div className="size-8 rounded-lg bg-brand/10 grid place-items-center text-brand"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d={card.icon} /></svg></div><span className="text-xs font-medium">{card.title}</span></div>
                      <p className="text-xs text-muted-foreground mt-1">{card.desc}</p>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* 5.13 错误与重试 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">5.13 错误与重试</div>
              <div className="p-6 space-y-4">
                {/* AI 出错 */}
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4">
                  <div className="flex items-center gap-2"><svg className="size-5 text-destructive" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /><path d="M12 9v4M12 17h.01" /></svg><span className="text-xs font-medium">生成失败</span></div>
                  <div className="text-xs text-muted-foreground mt-1">API 连接超时，请检查网络后重试。</div>
                  <div className="flex items-center gap-2 mt-3">
                    <button type="button" className="inline-flex items-center h-8 px-3 rounded-md border border-border bg-background text-xs hover:bg-accent">重试</button>
                    <button type="button" className="inline-flex items-center h-8 px-3 rounded-md text-xs text-muted-foreground hover:bg-accent">换模型重试</button>
                  </div>
                </div>
                {/* 网络断开 */}
                <div className="rounded-md bg-warning/10 border-b border-warning/30 text-warning text-xs py-2 text-center px-4 flex items-center justify-center gap-3">网络连接已断开<button type="button" className="h-7 px-2 rounded-md border border-warning/30 bg-background text-warning text-xs hover:bg-warning/20">重连</button></div>
                {/* 超限 */}
                <div className="rounded-md bg-info/10 border border-info/30 text-info text-xs py-2 px-4 flex items-center justify-between">上下文长度已达上限，部分早期消息将不纳入上下文。<button type="button" className="h-7 px-2 rounded-md border border-info/30 bg-background text-info text-xs hover:bg-info/20">升级</button></div>
              </div>
            </div>

            {/* 5.14 键盘快捷键 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">5.14 键盘快捷键</div>
              <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-2">
                {[
                  { key: '⌘K', label: '命令面板' },
                  { key: '⌘⇧O', label: '新建会话' },
                  { key: '⌘/', label: '聚焦输入框' },
                  { key: '⌘↑', label: '编辑上一条用户消息' },
                  { key: '⌘⇧↵', label: '重新生成最后一条回复' },
                  { key: '↵', label: '发送' },
                  { key: '⇧↵', label: '换行' },
                  { key: 'Esc', label: '关闭弹层 / 取消编辑' },
                  { key: '⌘B', label: '折叠/展开左侧会话列表' },
                  { key: '⌘.', label: '折叠/展开右侧面板' },
                ].map((s) => (
                  <div key={s.key} className="flex items-center justify-between border-b border-border py-1.5">
                    <span className="text-xs text-muted-foreground">{s.label}</span>
                    <kbd className="px-2 py-0.5 rounded border border-border bg-muted text-xs font-mono"><span className="text-[10px]">{s.key}</span></kbd>
                  </div>
                ))}
              </div>
            </div>

            {/* 5.15 上下文用量三态 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">5.15 上下文用量（三态）</div>
              <div className="p-6 space-y-3">
                <div className="flex items-center gap-3"><div className="flex-1 h-1 rounded-full bg-muted overflow-hidden"><div className="h-full bg-brand" style={{ width: '42%' }} /></div><span className="text-xs text-muted-foreground tabular-nums">42% · 正常</span></div>
                <div className="flex items-center gap-3"><div className="flex-1 h-1 rounded-full bg-muted overflow-hidden"><div className="h-full bg-warning" style={{ width: '84%' }} /></div><span className="text-xs text-warning tabular-nums">84% · 接近上限</span></div>
                <div className="flex items-center gap-3"><div className="flex-1 h-1 rounded-full bg-muted overflow-hidden"><div className="h-full bg-destructive" style={{ width: '97%' }} /></div><span className="text-xs text-destructive tabular-nums">97% · 即将超限</span></div>
              </div>
            </div>

            {/* 5.16 会话级操作 + 5.17 角色 */}
            <div className="grid md:grid-cols-2 gap-4">
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">5.16 会话级操作（分享弹层）</h3>
                <div className="w-64 rounded-lg border border-border bg-popover p-1 shadow-md">
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.59 13.51 6.83 3.98M15.41 6.51l-6.82 3.98" /></svg>生成分享链接</button>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13" /></svg>导出 Markdown</button>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /></svg>导出 JSON</button>
                  <button type="button" className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent text-xs"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="18" height="18" x="3" y="3" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21" /></svg>导出为图片</button>
                </div>
              </div>
              <div className="rounded-lg border border-border bg-card p-6">
                <h3 className="text-xs font-medium text-muted-foreground mb-3">5.17 多模型 + 角色人设</h3>
                <div className="flex flex-wrap gap-2 mb-4">
                  <button type="button" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-primary text-primary-foreground text-[10px] font-medium">代码助手</button>
                  <button type="button" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-border bg-background text-[10px] hover:bg-accent">翻译官</button>
                  <button type="button" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-border bg-background text-[10px] hover:bg-accent">文案</button>
                  <button type="button" className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full border border-border bg-background text-[10px] hover:bg-accent">+ 自定义</button>
                </div>
                <label className="text-xs font-medium">系统提示词</label>
                <textarea className="w-full mt-1.5 rounded-md border border-border bg-background px-3 py-2 text-xs min-h-[80px]">你是一位资深全栈工程师，回答简洁、给可运行代码。</textarea>
                <div className="flex items-center justify-between mt-3"><span className="text-xs text-muted-foreground">温度</span><div className="flex items-center gap-2"><input type="range" className="w-32 accent-brand" min="0" max="2" step="0.1" defaultValue="0.3" /><span className="text-xs text-muted-foreground tabular-nums w-6">0.3</span></div></div>
              </div>
            </div>

            {/* 5.5 消息分支导航 + 5.6 流式光标 */}
            <div className="rounded-lg border border-border bg-card p-6 space-y-4">
              <h3 className="text-xs font-medium text-muted-foreground">5.5 分支导航 + 5.6 流式光标</h3>
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                <button type="button" className="size-6 rounded-md hover:bg-accent grid place-items-center opacity-40 pointer-events-none"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m15 18-6-6 6-6" /></svg></button>
                <span className="tabular-nums">1 / 3</span>
                <button type="button" className="size-6 rounded-md hover:bg-accent grid place-items-center"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 18 6-6-6-6" /></svg></button>
                <span className="ml-2">分支回复导航</span>
              </div>
              <div className="text-xs text-foreground">Bot 正在生成回复<span className="pulse-cursor" /></div>
              <div className="flex items-center gap-2">
                <button type="button" className="inline-flex items-center justify-center size-9 rounded-md border border-destructive/30 text-destructive hover:bg-destructive/10"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="12" height="10" x="6" y="7" rx="2" /></svg></button>
                <span className="text-xs text-muted-foreground">停止生成按钮（流式中替换发送按钮）</span>
              </div>
            </div>
          </div>
        )}

        {/* ════════════════ 对话协作页 §5 ════════════════ */}
        {activePage === 'chat' && (
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-6 py-3 border-b border-border text-xs font-medium text-muted-foreground">三栏对话布局 · 会话列表 / 消息区 / 上下文面板</div>
            <div className="flex h-[600px]">
              {/* 左：会话列表 §5.1 */}
              <aside className="w-64 border-r border-border bg-muted/30 shrink-0 flex flex-col">
                <div className="p-3 border-b border-border"><button type="button" className="w-full h-9 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 transition-opacity flex items-center justify-center gap-2"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>新建会话</button></div>
                <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
                  <div className="text-xs font-medium text-muted-foreground px-2 py-1">今天</div>
                  {['Skill报错排查', '部署问题', '数据库迁移方案'].map((title, i) => (
                    <button type="button" key={title} className={`w-full text-left px-3 py-2 rounded-md transition-colors ${i === 0 ? 'bg-secondary' : 'hover:bg-accent'}`}>
                      <div className="text-xs font-medium truncate">{title}</div>
                      <div className="text-xs text-muted-foreground truncate mt-0.5">{i === 0 ? '分析日志并修复…' : '灰度环境…'}</div>
                      <div className="text-xs text-muted-foreground mt-1">{i === 0 ? '刚刚' : i === 1 ? '09:12' : '08:30'}</div>
                    </button>
                  ))}
                  <div className="text-xs font-medium text-muted-foreground px-2 py-1 mt-2">昨天</div>
                  <button type="button" className="w-full text-left px-3 py-2 rounded-md hover:bg-accent transition-colors"><div className="text-xs font-medium truncate">权限配置讨论</div><div className="text-xs text-muted-foreground truncate mt-0.5">RBAC 角色与资源…</div><div className="text-xs text-muted-foreground mt-1">昨天 16:40</div></button>
                </div>
                <div className="flex-none p-3 border-t border-border">
                  <div className="flex items-center justify-between mb-1.5"><span className="text-xs text-muted-foreground">上下文用量</span><span className="text-xs text-muted-foreground tabular-nums">42%</span></div>
                  <div className="h-1 rounded-full bg-muted overflow-hidden"><div className="h-full bg-brand" style={{ width: '42%' }} /></div>
                </div>
              </aside>

              {/* 中：消息区 §5.2-5.6 */}
              <main className="flex-1 flex flex-col min-w-0">
                <div className="flex-none h-14 border-b border-border flex items-center px-6 gap-3">
                  <div className="flex items-center gap-2">
                    <h1 className="text-xs font-medium truncate">Skill报错排查</h1>
                    <span className="inline-flex items-center h-5 px-2 rounded-md bg-secondary text-secondary-foreground text-xs font-medium">Skill Bot</span>
                    <span className="inline-flex items-center h-5 px-2 rounded-md bg-secondary text-secondary-foreground text-xs">GPT-4o</span>
                  </div>
                  <div className="ml-auto flex items-center gap-0.5">
                    <button type="button" className="size-8 rounded-md hover:bg-accent grid place-items-center text-muted-foreground"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13" /></svg></button>
                    <button type="button" className="size-8 rounded-md hover:bg-accent grid place-items-center text-muted-foreground"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg></button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto">
                  <div className="max-w-3xl mx-auto px-4 py-6">
                    {/* 用户消息 §5.3 */}
                    <div className="msg-group py-4">
                      <div className="flex gap-3">
                        <div className="size-8 rounded-full bg-brand/15 text-brand grid place-items-center text-[10px] font-medium flex-none">C</div>
                        <div className="min-w-0 flex-1">
                          <div className="text-xs text-muted-foreground mb-1">You · 2分钟前</div>
                          <div className="text-xs whitespace-pre-wrap break-words">帮我看一下这个 Skill 的执行报错，附上报错日志</div>
                          <div className="mt-2 flex flex-wrap gap-2">
                            <div className="inline-flex items-center gap-2 h-8 pl-2 pr-1 rounded-md border border-border bg-card"><svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg><span className="text-xs truncate max-w-[160px]">error.log</span><span className="text-xs text-muted-foreground">2.4 KB</span></div>
                          </div>
                          {/* 用户消息操作行 §5.4：编辑/复制/删除 */}
                          <div className="msg-actions mt-2 flex items-center gap-0.5 text-muted-foreground">
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="编辑"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="复制"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg></button>
                          </div>
                        </div>
                      </div>
                    </div>
                    {/* AI 消息 §5.3 §5.7 §5.4 */}
                    <div className="msg-group py-4">
                      <div className="flex gap-3">
                        <div className="size-8 rounded-full bg-zinc-900 text-zinc-50 grid place-items-center flex-none"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 8V4H8M4 8h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-8Z" /><path d="M2 14h20" /></svg></div>
                        <div className="min-w-0 flex-1 space-y-3">
                          <div className="text-xs text-muted-foreground mb-1">GPT-4o · 2分钟前</div>
                          {/* 思考过程 §5.7 */}
                          <div className={`collapsible ${collapsibleOpen ? 'open' : ''} rounded-lg border border-border bg-muted/30 px-3 py-2 my-2`}>
                            <button type="button" className="collap-head w-full flex items-center gap-2 text-left" onClick={() => setCollapsibleOpen(!collapsibleOpen)}>
                              <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" /><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" /></svg>
                              <span className="text-xs font-medium">思考过程</span>
                              <span className="text-xs text-muted-foreground">用时 1.6s</span>
                              <svg className="chev size-4 text-muted-foreground ml-auto" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>
                            </button>
                            <div className="collap-body mt-2 text-xs text-muted-foreground italic leading-relaxed">用户附带了 error.log，需要先解析堆栈，定位到 validateStep 函数的空值问题…</div>
                          </div>
                          {/* 正文 */}
                          <div className="text-xs leading-6">
                            根据日志分析，<code className="font-mono bg-muted px-1.5 py-0.5 rounded text-xs">validateStep</code> 在处理空输入时抛出 <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-xs">TypeError</code>。以下是修复补丁<sup className="text-xs text-brand align-super cursor-pointer hover:underline">[1]</sup>：
                          </div>
                          {/* 代码块 §5.3 */}
                          <div className="rounded-lg bg-zinc-950 text-zinc-50 overflow-hidden">
                            <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800"><span className="text-xs text-zinc-400 font-mono">validate-step.ts</span><button type="button" className="size-7 rounded-md hover:bg-zinc-800 grid place-items-center text-zinc-400"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg></button></div>
                            <pre className="px-3 py-2.5 text-xs font-mono overflow-x-auto leading-relaxed"><code><span className="text-sky-400">function</span> <span className="text-emerald-400">validateStep</span>(ctx) {'{'}
  <span className="text-sky-400">if</span> (ctx.skip) <span className="text-sky-400">return</span> [];
  <span className="text-zinc-500">{'// 修复：空值兜底'}</span>
  <span className="text-sky-400">const</span> input = ctx.step.input ?? <span className="text-amber-300">{`""`}</span>;
  <span className="text-sky-400">return</span> input.split(<span className="text-amber-300">{`","`}</span>).map(p {'='}{'>'} p.trim()).filter(Boolean);
{'}'}</code></pre>
                          </div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="inline-flex items-center rounded-full border border-success/30 bg-success/10 px-2.5 py-0.5 text-[10px] font-medium text-success">已修复</span>
                            <span className="text-xs text-muted-foreground">引用来源 2 条</span>
                          </div>
                          {/* AI 消息操作工具栏 §5.4：复制/重生成/赞踩/朗读/分享/引用/收藏 */}
                          <div className="msg-actions flex items-center gap-0.5 text-muted-foreground">
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="复制"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="重新生成"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1.06 6.7 2.82L21 8" /><path d="M21 3v5h-5" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="点赞"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3 3 0 0 1 3 3 2 2 0 0 1 0 .88Z" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="点踩"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 14V2M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3 3 0 0 1-3-3 2 2 0 0 1 0-.88Z" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="朗读"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 5 6 9H2v6h4l5 4V5zM15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="引用回复"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" /></svg></button>
                            <button type="button" className="size-7 rounded-md hover:bg-accent hover:text-foreground transition-colors" title="收藏"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" /></svg></button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                {/* 输入区 §5.10 */}
                <div className="flex-none p-4 border-t border-border">
                  <div className="rounded-2xl border border-border shadow-sm bg-background p-2">
                    <textarea placeholder="给 tc 助手发消息…" className="w-full bg-transparent px-2 py-2 text-xs outline-none resize-none min-h-[56px] max-h-[200px]" defaultValue="生成迁移脚本，参考架构图.png" />
                    <div className="flex items-center justify-between px-1">
                      <div className="flex items-center gap-0.5">
                        <button type="button" className="size-8 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="附件"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg></button>
                        <button type="button" className="inline-flex items-center gap-1.5 h-8 px-2 rounded-md hover:bg-accent transition-colors text-xs text-muted-foreground">GPT-4o <svg className="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg></button>
                        <button type="button" className="size-8 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="斜杠命令"><span className="text-xs">/</span></button>
                        <button type="button" className="size-8 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="语音"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="12" height="10" x="6" y="7" rx="2" /><path d="M11 2v3M11 19v3M9 7v1h6V7" /></svg></button>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-xs text-muted-foreground tabular-nums">~128 tokens</span>
                        <button type="button" className="inline-flex items-center justify-center size-9 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity" aria-label="发送"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></svg></button>
                      </div>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground text-center mt-2">Enter 发送 · Shift+Enter 换行</p>
                </div>
              </main>

              {/* 右：艺术品/引用面板 §5.9 */}
              <aside className="w-80 border-l border-border shrink-0 hidden xl:flex flex-col bg-background">
                <div className="flex-none h-12 border-b border-border flex items-center px-3 gap-0.5">
                  <button type="button" className="h-8 px-3 rounded-md text-xs font-medium bg-secondary text-secondary-foreground">代码</button>
                  <button type="button" className="h-8 px-3 rounded-md text-xs text-muted-foreground hover:bg-accent transition-colors">预览</button>
                  <button type="button" className="h-8 px-3 rounded-md text-xs text-muted-foreground hover:bg-accent transition-colors">文档</button>
                  <div className="ml-auto flex items-center gap-0.5">
                    <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="下载"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" /></svg></button>
                    <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="复制"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="14" height="14" x="8" y="8" rx="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></svg></button>
                    <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="全屏"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" /></svg></button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto p-3">
                  <div className="rounded-lg bg-zinc-950 text-zinc-50 overflow-hidden">
                    <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800"><span className="text-xs text-zinc-400 font-mono">validate-step.ts</span><span className="text-xs text-zinc-500">248 行</span></div>
                    <pre className="px-3 py-2.5 text-xs font-mono overflow-x-auto leading-relaxed"><code><span className="text-sky-400">function</span> <span className="text-emerald-400">validateStep</span>(ctx) {'{'}
  <span className="text-sky-400">if</span> (ctx.skip) <span className="text-sky-400">return</span> [];
  <span className="text-zinc-500">{'// 修复：空值兜底'}</span>
  <span className="text-sky-400">const</span> input = ctx.step.input ?? <span className="text-amber-300">{`""`}</span>;
  <span className="text-sky-400">return</span> input.split(<span className="text-amber-300">{`","`}</span>).map(p {'='}{'>'} p.trim()).filter(Boolean);
{'}'}</code></pre>
                  </div>
                </div>
                {/* 引用来源 §5.8 */}
                <div className="border-t border-border p-3 shrink-0">
                  <div className="text-xs font-medium text-muted-foreground mb-2">引用来源</div>
                  <div className="space-y-1.5">
                    <button type="button" className="w-full text-left rounded-md border border-border bg-card p-2.5 hover:bg-accent hover:border-brand/40 transition-colors">
                      <div className="flex items-start gap-2">
                        <span className="text-xs text-brand font-medium tabular-nums mt-0.5">[1]</span>
                        <div className="min-w-0 flex-1"><div className="text-xs font-medium truncate">MongoDB 覆盖索引最佳实践</div><div className="text-xs text-muted-foreground truncate mt-0.5">mongodb.com</div></div>
                      </div>
                    </button>
                    <button type="button" className="w-full text-left rounded-md border border-border bg-card p-2.5 hover:bg-accent hover:border-brand/40 transition-colors">
                      <div className="flex items-start gap-2">
                        <span className="text-xs text-brand font-medium tabular-nums mt-0.5">[2]</span>
                        <div className="min-w-0 flex-1"><div className="text-xs font-medium truncate">流式序列化性能对比</div><div className="text-xs text-muted-foreground truncate mt-0.5">github.com</div></div>
                      </div>
                    </button>
                  </div>
                </div>
              </aside>
            </div>
          </div>
        )}

        {/* ════════════════ Bot 工作台 §3.3b EntityCard ════════════════ */}
        {activePage === 'bot' && (
          <div className="space-y-4">
            {/* PageHeader §4.5 */}
            <div className="flex items-start justify-between gap-4 mb-6">
              <div className="space-y-1">
                <h1 className="text-2xl font-semibold tracking-tight">Bot 工坊</h1>
                <p className="text-xs text-muted-foreground">管理和发布 AI Bot</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button type="button" className={btn.outline}>导入</button>
                <button type="button" className={btn.default}>创建 Bot</button>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {[
                { name: '代码审查 Bot', desc: '自动审查 PR 代码质量，支持多语言和自定义规则。', status: '运行中', statusColor: 'success', tags: ['团队', '云端', '服务'], skill: 'CodeReview', version: 'v1.2', health: 95 },
                { name: '会议纪要 Bot', desc: '自动转录会议音频，生成结构化纪要和待办事项。', status: '已停止', statusColor: 'secondary', tags: ['团队', '云端'], skill: 'MeetingNotes', version: 'v0.9', health: 0 },
                { name: '翻译助手 Bot', desc: '实时多语言翻译，保持术语一致性。', status: '运行中', statusColor: 'success', tags: ['个人', '本地'], skill: 'Translate', version: 'v1.0', health: 88 },
              ].map((bot) => (
                <div key={bot.name} className="rounded-lg border border-border bg-card p-5 shadow-sm cursor-pointer hover:shadow hover:border-brand/40 transition-all duration-200">
                  {/* 头部 */}
                  <div className="flex gap-4 mb-3">
                    <div className="size-12 rounded-xl bg-brand grid place-items-center shrink-0"><svg className="size-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 8V4H8M4 8h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-8Z" /><path d="M2 14h20" /></svg></div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-base font-semibold">{bot.name}</span>
                        <span className={`inline-flex items-center rounded-full ${bot.statusColor === 'success' ? 'border border-success/30 bg-success/10 text-success' : 'bg-secondary text-secondary-foreground'} px-2 py-0.5 text-[10px] font-medium`}>{bot.status} · {bot.version}</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {bot.tags.map((tag) => (
                          <span key={tag} className={`inline-flex items-center rounded-full ${tag === '云端' ? 'border border-info/30 bg-info/10 text-info' : tag === '服务' ? 'border border-success/30 bg-success/10 text-success' : tag === '本地' ? 'border border-brand/30 bg-brand/10 text-brand' : 'bg-secondary text-secondary-foreground'} px-2 py-0.5 text-[10px] font-medium`}>{tag}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                  {/* 描述 */}
                  <p className="text-xs text-muted-foreground leading-5 min-h-[40px]">{bot.desc}</p>
                  {/* 指标行 */}
                  <div className="flex items-center gap-2 mt-3 flex-wrap">
                    {bot.health > 0 && (
                      <span className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium tabular-nums ${bot.health >= 90 ? 'bg-success/10 text-success' : bot.health >= 70 ? 'bg-brand/10 text-brand' : 'bg-destructive/10 text-destructive'}`}>
                        <span className="size-1.5 rounded-full bg-current" />健康分 {bot.health}
                      </span>
                    )}
                    <span className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" /></svg>{bot.skill}</span>
                  </div>
                  {/* 操作区 §3.3b */}
                  <div className="border-t border-border/50 pt-3 mt-3 flex items-center justify-between">
                    <div className="flex flex-wrap gap-2">
                      <button type="button" className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md bg-primary text-primary-foreground text-xs hover:opacity-90 transition-opacity">对话</button>
                      <button type="button" className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border bg-background text-xs hover:bg-accent transition-colors"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>编辑</button>
                      <button type="button" className="inline-flex items-center gap-1 h-7 px-2.5 rounded-md border border-border bg-background text-xs hover:bg-accent transition-colors"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>查看</button>
                    </div>
                    <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" /></svg></button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ════════════════ 管理后台 §4.5-4.9 ════════════════ */}
        {/* ════════════════ 管理后台 ════════════════ */}
        {activePage === 'admin' && (
          <div className="space-y-6">

            {/* 顶栏 §4.1 h-14 */}
            <div className="sticky top-14 z-40 h-14 border-b border-border bg-background/80 backdrop-blur flex items-center px-4 md:px-6 gap-3 shrink-0 rounded-lg">
              {/* 面包屑 */}
              <nav className="flex items-center gap-1.5 text-xs">
                <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" /></svg>
                <span className="text-muted-foreground">管理</span>
                <span className="text-muted-foreground">/</span>
                <span className="font-medium">管理后台</span>
              </nav>
              {/* 右侧操作区 */}
              <div className="ml-auto flex items-center gap-2">
                <button type="button" className="inline-flex items-center gap-2 h-9 px-3 rounded-md border border-border bg-background text-xs text-muted-foreground hover:bg-accent transition-colors">
                  <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
                  <span>搜索...</span>
                  <kbd className="px-1.5 py-0.5 rounded border border-border bg-muted text-[10px] font-mono">⌘K</kbd>
                </button>
                <button type="button" className="relative inline-flex items-center justify-center size-9 rounded-md border border-border bg-background hover:bg-accent transition-colors" aria-label="通知">
                  <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></svg>
                  <span className="absolute top-2 right-2 size-2 rounded-full bg-destructive ring-2 ring-background" />
                </button>
                <button type="button" className="size-9 rounded-full bg-brand/15 grid place-items-center text-[10px] font-medium text-brand hover:opacity-90 transition-opacity" aria-label="用户菜单">C</button>
              </div>
            </div>

            {/* PageHeader §4.5 */}
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1">
                <h1 className="text-2xl font-semibold tracking-tight">空间管理</h1>
                <p className="text-xs text-muted-foreground">管理团队空间和成员权限</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button type="button" className={btn.default + ' flex items-center gap-2'}>
                  <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
                  新建空间
                </button>
              </div>
            </div>

            {/* Tabs §3.x */}
            <div className="border-b border-border">
              <div className="flex items-center gap-1">
                <div className="tab-item active">空间列表</div>
                <div className="tab-item">成员管理</div>
                <div className="tab-item">资源统计</div>
              </div>
            </div>

            {/* 统计卡片行 */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">活跃空间</span>
                  <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M9 22V12h6v10" /></svg>
                </div>
                <div className="text-2xl font-semibold tabular-nums mt-2">12</div>
                <div className="flex items-center gap-1 text-xs mt-1"><span className="text-success">+2</span><span className="text-muted-foreground">vs 上月</span></div>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">成员总数</span>
                  <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>
                </div>
                <div className="text-2xl font-semibold tabular-nums mt-2">86</div>
                <div className="flex items-center gap-1 text-xs mt-1"><span className="text-success">+8</span><span className="text-muted-foreground">vs 上月</span></div>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">资源总数</span>
                  <svg className="size-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" /></svg>
                </div>
                <div className="text-2xl font-semibold tabular-nums mt-2">248</div>
                <div className="flex items-center gap-1 text-xs mt-1"><span className="text-success">+24</span><span className="text-muted-foreground">vs 上月</span></div>
              </div>
            </div>

            {/* 空间列表表格 §3.5 */}
            <div className="rounded-lg border border-border bg-card overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="h-10 text-xs font-medium text-muted-foreground uppercase tracking-wider border-b border-border">
                    <th className="text-left px-4">空间名</th>
                    <th className="text-left px-4">成员数</th>
                    <th className="text-left px-4">资源数</th>
                    <th className="text-left px-4 hidden md:table-cell">创建时间</th>
                    <th className="text-left px-4">状态</th>
                    <th className="text-right px-4">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { name: '技术中台', members: 24, resources: 68, date: '2026-07-12', status: '活跃', statusColor: 'success' },
                    { name: '产品研发', members: 18, resources: 42, date: '2026-07-20', status: '活跃', statusColor: 'success' },
                    { name: '运营增长', members: 12, resources: 36, date: '2026-08-01', status: '活跃', statusColor: 'success' },
                    { name: '数据分析', members: 8, resources: 24, date: '2026-08-05', status: '待审核', statusColor: 'warning' },
                    { name: '安全合规', members: 6, resources: 12, date: '2026-08-08', status: '已归档', statusColor: 'muted' },
                  ].map((row) => (
                    <tr key={row.name} className="h-10 hover:bg-muted/50 transition-colors border-b border-border last:border-0">
                      <td className="px-4 text-xs font-medium">{row.name}</td>
                      <td className="px-4 text-xs tabular-nums">{row.members}</td>
                      <td className="px-4 text-xs tabular-nums">{row.resources}</td>
                      <td className="px-4 text-xs text-muted-foreground hidden md:table-cell">{row.date}</td>
                      <td className="px-4">
                        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${row.statusColor === 'success' ? 'border-success/30 bg-success/10 text-success' : row.statusColor === 'warning' ? 'border-warning/30 bg-warning/10 text-warning' : 'border-border bg-secondary text-muted-foreground'}`}>{row.status}</span>
                      </td>
                      <td className="px-4 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="编辑"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg></button>
                          <button type="button" className="size-7 rounded-md hover:bg-accent grid place-items-center text-muted-foreground" title="查看"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg></button>
                          <button type="button" className="size-7 rounded-md hover:bg-destructive/10 grid place-items-center text-muted-foreground hover:text-destructive" title="删除"><svg className="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* 分页栏 */}
              <div className="flex items-center justify-between px-4 py-3 border-t border-border">
                <span className="text-xs text-muted-foreground">共 8 条 · 第 1-5 条</span>
                <div className="flex items-center gap-1">
                  <button type="button" className="size-9 rounded-md border border-border bg-background grid place-items-center hover:bg-accent text-muted-foreground"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m15 18-6-6 6-6" /></svg></button>
                  <button type="button" className="size-9 rounded-md bg-primary text-primary-foreground text-xs">1</button>
                  <button type="button" className="size-9 rounded-md hover:bg-accent text-xs">2</button>
                  <button type="button" className="size-9 rounded-md border border-border bg-background grid place-items-center hover:bg-accent"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 18 6-6-6-6" /></svg></button>
                </div>
              </div>
            </div>

            {/* 审批管理区域 */}
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <h2 className="text-lg font-semibold">待审批</h2>
              </div>

              {/* Alert §3.17 info */}
              <div className="rounded-lg border border-info/30 bg-info/5 p-4 flex gap-3">
                <svg className="size-5 shrink-0 mt-0.5 text-info" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg>
                <div className="flex-1">
                  <div className="text-xs font-medium">有 3 条待审批请求，请及时处理</div>
                  <div className="text-xs text-muted-foreground mt-0.5">下方列出全部待处理项，处理后将自动移除。</div>
                </div>
                <button type="button" className="inline-flex items-center justify-center size-7 rounded-md hover:bg-accent text-muted-foreground shrink-0"><svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg></button>
              </div>

              {/* 审批卡片列表 */}
              <div className="space-y-3">
                {[
                  { name: '张三', email: 'zhangsan@teamclaw.com', initial: '张', target: '技术中台', desc: '作为开发者参与空间内资源协作', time: '5 分钟前' },
                  { name: '李四', email: 'lisi@teamclaw.com', initial: '李', target: '产品研发', desc: '作为产品经理参与需求评审', time: '20 分钟前' },
                  { name: '王五', email: 'wangwu@teamclaw.com', initial: '王', target: '数据分析', desc: '作为分析师查看数据看板', time: '1 小时前' },
                ].map((item) => (
                  <div key={item.name} className="approval-card rounded-lg border border-border bg-card p-4 shadow-sm hover:shadow">
                    <div className="flex items-center gap-4">
                      {/* 申请人 */}
                      <div className="flex items-center gap-3 shrink-0">
                        <div className="size-10 rounded-full bg-brand/15 grid place-items-center text-[10px] font-medium text-brand">{item.initial}</div>
                        <div className="hidden sm:block">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium">{item.name}</span>
                            <span className="inline-flex items-center rounded-full border border-info/30 bg-info/10 px-2 py-0.5 text-[10px] font-semibold text-info">团队成员</span>
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5">{item.email}</div>
                        </div>
                      </div>
                      {/* 描述 */}
                      <div className="flex-1 min-w-0 hidden md:block">
                        <div className="text-xs">申请加入 <span className="font-medium">{item.target}</span> 团队</div>
                        <div className="text-xs text-muted-foreground mt-0.5">{item.desc}</div>
                      </div>
                      <div className="flex-1 min-w-0 md:hidden">
                        <div className="text-xs">申请加入 <span className="font-medium">{item.target}</span></div>
                      </div>
                      {/* 操作 + 时间 */}
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-xs text-muted-foreground hidden lg:inline">{item.time}</span>
                        <button type="button" className="inline-flex items-center justify-center h-8 px-3 rounded-md border border-border bg-background text-xs font-medium hover:bg-accent transition-colors">拒绝</button>
                        <button type="button" className="inline-flex items-center justify-center h-8 px-3 rounded-md bg-primary text-primary-foreground text-xs font-medium hover:opacity-90 transition-opacity">同意</button>
                      </div>
                    </div>
                    <div className="text-xs text-muted-foreground mt-2 lg:hidden">{item.time}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* 保存栏演示 §4.9 */}
            <div className="rounded-lg border border-border bg-card p-6">
              <div className="space-y-1 mb-4">
                <h3 className="text-lg font-semibold">保存栏示例</h3>
                <p className="text-xs text-muted-foreground">用于「通知设置」等表单页，当前空间列表页不展示此栏。下方为规范演示：</p>
              </div>
              <div className="rounded-lg border border-border bg-background">
                <div className="sticky bottom-0 bg-background/80 backdrop-blur border-t border-border py-3 px-4 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <svg className="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg>
                    有未保存的更改
                  </div>
                  <div className="flex items-center gap-2">
                    <button type="button" className={btn.outline}>重置</button>
                    <button type="button" className={btn.default}>保存</button>
                  </div>
                </div>
              </div>
            </div>

          </div>
        )}

      </div>
    </div>
  );
};

export default DesignSystem;
