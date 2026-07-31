import { sketchRelativeDir } from "./paths";

/** One-click paste for terminal AI after export (constraint #7). */
export function buildTerminalOpener(sketchId: string): string {
  const dir = sketchRelativeDir(sketchId);
  return [
    `請讀 ${dir} 入面嘅 INSTRUCTIONS.md、meta.yaml 同四張圖。`,
    "呢份指令書自包含——唔需要訪問任何其他文件。",
    "有唔明就問我，唔准靜靜哋估。",
    "雙方同意之後，先寫 strategy.yaml 落同一個資料夾。",
  ].join("\n");
}
