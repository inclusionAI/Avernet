import { getCapabilities } from '@/capabilities';
import { GITHUB_REPO_URL } from '../constants';

/** 欢迎页页脚:brand Logo + GitHub 外链。 */
export function WelcomeFooter() {
  const brand = getCapabilities().getProductBrand().value;
  return (
    <footer className="border-t border-border">
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
