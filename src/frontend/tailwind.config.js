module.exports = {
  darkMode: ['class'],
  content: [
    './src/pages/**/*.tsx',
    './src/components/**/*.tsx',
    './src/layouts/**/*.tsx',
    './src/styles/**/*.ts',
  ],
  theme: {
    extend: {
      colors: {
        // shadcn CSS 变量色彩（与 lavender 色板并存）
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        lavender: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
        },
      },
      borderRadius: {
        none: '0',
        sm: '2px',
        DEFAULT: '4px',
        md: '6px',
        lg: '8px',
        xl: '10px',
        '2xl': '12px',
        '3xl': '14px',
        full: '9999px',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        blink: { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0' } },
        shimmer: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        // 铃铛摇晃：左右小幅摆动，模拟响铃，用于「待回复」状态
        wiggle: {
          '0%, 60%, 100%': { transform: 'rotate(0deg)' },
          '15%': { transform: 'rotate(-12deg)' },
          '30%': { transform: 'rotate(10deg)' },
          '45%': { transform: 'rotate(-6deg)' },
        },
        // BCN 首页场景 mockup 入场/浮动/脉冲（淡入上移 / 轻微浮动 / 柔和脉冲）
        'guide-pop': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'guide-float': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-4px)' },
        },
        'scenario-pulse': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(99,102,241,0)' },
          '50%': { boxShadow: '0 0 0 4px rgba(99,102,241,0.18)' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        blink: 'blink 1s steps(2) infinite',
        shimmer: 'shimmer 3s ease-in-out infinite',
        wiggle: 'wiggle 1s ease-in-out infinite',
        'guide-pop': 'guide-pop 0.4s ease-out both',
        'guide-float': 'guide-float 3s ease-in-out infinite',
        'scenario-pulse': 'scenario-pulse 2.4s ease-in-out infinite',
      },
      fontSize: {
        // 字体规范：
        //   xs   = 12px — 时间戳、标签、角标等辅助信息
        //   sm   = 13px — 正文、列表内容、表单标签（全局主体字号）
        //   base = 14px — 模块标题、次级标题
        //   lg   = 16px — 页面大标题
        xs: [
          '12px',
          {
            lineHeight: '18px',
            fontWeight: '400',
          },
        ],
        sm: [
          '13px',
          {
            lineHeight: '20px',
            fontWeight: '400',
          },
        ],
        base: [
          '14px',
          {
            lineHeight: '22px',
            fontWeight: '400',
          },
        ],
        lg: [
          '16px',
          {
            lineHeight: '24px',
            fontWeight: '400',
          },
        ],
        xl: [
          '18px',
          {
            lineHeight: '28px',
            fontWeight: '400',
          },
        ],
        '2xl': [
          '22px',
          {
            lineHeight: '32px',
            fontWeight: '400',
          },
        ],
        '3xl': [
          '28px',
          {
            lineHeight: '36px',
            fontWeight: '400',
          },
        ],
      },
      boxShadow: {
        // 自定义阴影：左侧导航栏右侧阴影
        'nav-right': '2px 0 6px rgba(0, 0, 0, 0.03)',
      },
    },
  },
  plugins: [require('@tailwindcss/typography'), require('tailwindcss-animate')],
};
