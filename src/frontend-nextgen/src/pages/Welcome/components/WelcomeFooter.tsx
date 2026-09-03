import { getCapabilities } from '@/capabilities';
import { GITHUB_REPO_URL } from '../constants';

/** 欢迎页页脚:brand Logo + GitHub 外链。sticky bottom 钉在视口底部(根节点为滚动容器,
 * 见 ../index.tsx;不做循环动画);bg-background 不透明底避让背后滚过的内容。 */
export function WelcomeFooter() {
  const brand = getCapabilities().getProductBrand().value;
  return (
    <footer className="sticky bottom-0 z-30 border-t border-border bg-background">
      <div className="mx-auto flex w-full max-w-[1200px] flex-col items-center justify-between gap-4 px-8 py-5 md:flex-row md:items-center">
        <brand.Logo className="h-8 w-auto" />
        <a
          href={GITHUB_REPO_URL}
          target="_blank"
          rel="noreferrer"
          className="text-sm font-medium text-primary transition-colors"
        >
          GitHub
        </a>
      </div>
    </footer>
  );
}
