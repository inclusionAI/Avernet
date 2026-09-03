import { history } from '@umijs/max';
import { HeroSection } from './components/HeroSection';
import { WelcomeFooter } from './components/WelcomeFooter';
import { WelcomeHeader } from './components/WelcomeHeader';

/**
 * Open 形态对外欢迎页(产品门面,默认入口 `/`)。独立落地页:AppLayout 外,不进工作台 Shell/侧栏;
 * internal overlay 声明 `/` 与 `/welcome` 覆盖项取消本页路由(config/routes.internal.ts),
 * 仅 Open 形态可达,本仓本地 dev(internal 形态)不可见。
 * 一期范围:Header-lite + Hero + Footer;二期按需补接入方式(id="access",依赖本地引擎接入故事)
 * 与场景展示(id="scenarios",依赖真实产品截图素材),见 design Non-Goals。
 */
export default function Welcome() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <WelcomeHeader />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-8 pb-16 pt-12">
        {/* 二期挂载位:接入方式 <section id="access"> / 场景展示 <section id="scenarios"> */}
        <HeroSection onEnter={() => history.push('/workspace')} />
      </main>
      <WelcomeFooter />
    </div>
  );
}
