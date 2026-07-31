import type { NavItem } from "../nav";

interface StubPageProps {
  item: NavItem;
}

/** Generic page stub for 6-1 — real content lands in later WO-006 steps. */
export function StubPage({ item }: StubPageProps) {
  return (
    <>
      <header className="main__header">
        <h1 className="main__title">{item.label}</h1>
        <span className="main__subtitle">{item.subtitle}</span>
      </header>
      <section className="stub-panel" aria-label={`${item.label} stub`}>
        <p className="stub-panel__kicker">
          {item.placeholder ? "Placeholder" : "Page stub"}
        </p>
        <p className="stub-panel__body">
          {item.placeholder
            ? "此導航項已預留路由與 shell 位置，功能將於對應 WO 落地；而家唔載業務邏輯。"
            : "6-1 基座 stub。數據與業務組件將於 6-2 起接入只讀 API；圖表序列一律由後端供應（D18）。"}
        </p>
        <p className="stub-panel__meta">route: {item.path}</p>
      </section>
    </>
  );
}
