/**
 * Mutation evidence for Cross-page Truth Correction A (D1-D3).
 * Every mutation is restored byte-for-byte before the next target runs.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

function runVitest(args) {
  const result = spawnSync(
    "npx",
    ["vitest", "run", ...args, "--reporter=dot"],
    {
      cwd: root,
      encoding: "utf8",
      shell: true,
    },
  );
  return {
    code: result.status ?? 1,
    output: (result.stdout || "") + (result.stderr || ""),
  };
}

function mutate(name, file, transform, vitestArgs) {
  const path = resolve(root, file);
  const original = readFileSync(path, "utf8");
  const mutated = transform(original);
  if (mutated === original) {
    console.log(`SKIP ${name} (mutation target not found)`);
    return false;
  }

  let result;
  try {
    writeFileSync(path, mutated);
    result = runVitest(vitestArgs);
  } finally {
    writeFileSync(path, original);
  }

  if (readFileSync(path, "utf8") !== original) {
    throw new Error(`${name}: mutation was not restored`);
  }

  const red = result.code !== 0;
  console.log(`${red ? "RED" : "GREEN(!)"} ${name} exit=${result.code}`);
  if (!red) {
    const summary = result.output
      .split("\n")
      .filter((line) => /Test Files|Tests|FAIL/.test(line))
      .slice(0, 6);
    console.log(summary.join("\n"));
  }
  return red;
}

const results = [
  [
    "D1 remove owner-review Library guard",
    mutate(
      "D1 remove owner-review Library guard",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source.replace(
          "if (ownerReview) {",
          "if (false && ownerReview) {",
        ),
      [
        "src/lib/p2/correctionB.test.tsx",
        "-t",
        "truth correction D1: Library is zero-fetch/storage/timer isolated",
      ],
    ),
  ],
  [
    "D2 restore stale costs and all-one slippage",
    mutate(
      "D2 restore stale costs and all-one slippage",
      "src/lib/backtest/types.ts",
      (source) =>
        source
          .replace(
            "fees: { NQ: 2.5, YM: 2.5, GC: 2.8 },",
            "fees: { NQ: 2.25, YM: 2.25, GC: 2.5 },",
          )
          .replace(
            "slippageTicks: { breakout: 1, stop: 2, target: 0, dayEnd: 1 },",
            "slippageTicks: { breakout: 1, stop: 1, target: 1, dayEnd: 1 },",
          ),
      [
        "src/lib/backtest/backtest.test.ts",
        "-t",
        "truth correction D2: approved defaults",
      ],
    ),
  ],
  [
    "D3 lowercase one canonical chart reference",
    mutate(
      "D3 lowercase one canonical chart reference",
      "src/lib/sketch/instructions-v1.template.md",
      (source) => source.replace("chart-D.png", "chart-d.png"),
      [
        "src/lib/sketch/sketch.test.ts",
        "-t",
        "truth correction D3: generated INSTRUCTIONS",
      ],
    ),
  ],
];

console.log("\n=== SUMMARY ===");
for (const [name, red] of results) {
  console.log(`${name}: ${red ? "RED ok" : "DID NOT FAIL"}`);
}

process.exit(results.some(([, red]) => !red) ? 1 : 0);
