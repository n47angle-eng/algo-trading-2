/**
 * Required mutation evidence for the P2 version-lifecycle seam and [177] §4.
 * Every temporary edit is restored byte-for-byte before the next mutation.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const requested = process.argv.slice(2);

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

function mutate(name, file, transform, testArgs) {
  if (
    requested.length > 0 &&
    !requested.some((fragment) => name.includes(fragment))
  ) {
    return null;
  }
  const path = resolve(root, file);
  const original = readFileSync(path, "utf8");
  const changed = transform(original);
  if (changed === original) {
    console.log(`SKIP ${name} (target not found)`);
    return false;
  }

  let result;
  try {
    writeFileSync(path, changed);
    result = runVitest(testArgs);
  } finally {
    writeFileSync(path, original);
  }
  if (readFileSync(path, "utf8") !== original) {
    throw new Error(`${name}: source was not restored`);
  }
  const red = result.code !== 0;
  console.log(`${red ? "RED" : "GREEN(!)"} ${name} exit=${result.code}`);
  if (!red) {
    console.log(
      result.output
        .split("\n")
        .filter((line) => /Tests|FAIL/.test(line))
        .slice(0, 5)
        .join("\n"),
    );
  }
  return red;
}

const results = [
  [
    "unknown references treated as a known zero",
    mutate(
      "unknown references treated as a known zero",
      "src/lib/strategy/lifecycleContract.ts",
      (source) =>
        source.replace(
          `  if (!list || !list.count_known || list.count === null) {
    return { kind: "unknown" };
  }`,
          `  if (!list || !list.count_known || list.count === null) {
    return { kind: "allowed" };
  }`,
        ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "reference lookup returns 503",
      ],
    ),
  ],
  [
    "derive request carries unchanged paths",
    mutate(
      "derive request carries unchanged paths",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source
          .replace(
            "if (raw === undefined || raw === row.value) {",
            "if (raw === undefined) {",
          )
          .replace(
            "if (String(value) === String(Number(row.value))) {",
            "if (false) {",
          ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "sends only the changed path",
      ],
    ),
  ],
  [
    "delete fires immediately instead of after the grace window",
    mutate(
      "delete fires immediately instead of after the grace window",
      "src/components/strategies/LibraryTab.tsx",
      (source) => source.replace("}, DELETE_GRACE_MS);", "}, 0);"),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "issues no DELETE during the grace window",
      ],
    ),
  ],
  [
    "unmount silently drops the pending delete",
    mutate(
      "unmount silently drops the pending delete",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source.replace(
          `        clearTimeout(item.timerId);
        void deleteStrategyVersion(item.strategyId, true).catch(() => {
          // Nothing can be shown after unmount; the server remains the truth.
        });`,
          `        clearTimeout(item.timerId);`,
        ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "flushes a pending delete",
      ],
    ),
  ],
  [
    "unverifiable derive response inserts a local version",
    mutate(
      "unverifiable derive response inserts a local version",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source.replace(
          `        if (!parsed) {
          // Never invent a local version from an unverifiable response.
          setError(
            "衍生結果未能核實，畫面唔會顯示未經確認嘅新版本；請重新讀取列表。",
          );
          return;
        }`,
          `        if (!parsed) {
          setVersions((prev) => [
            ...prev,
            (response.body as { version: StrategyVersion }).version,
          ]);
          return;
        }`,
        ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "derive response cannot be verified",
      ],
    ),
  ],
  [
    "derive 422 treated as a success path",
    mutate(
      "derive 422 treated as a success path",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source.replace(
          "if (response.status === 200 || response.status === 201) {",
          "if (response.status === 200 || response.status === 201 || response.status === 422) {",
        ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "verbatim 422 report",
      ],
    ),
  ],
  [
    "D0 warm-up copy reverted to the pre-revision 90-day/earlier-start text",
    mutate(
      "D0 warm-up copy reverted to the pre-revision 90-day/earlier-start text",
      "src/lib/backtest/fixtureStore.ts",
      (source) =>
        source
          .replace(
            '"「回踩 90EMA」嘅 Daily regime 實際需要 95 個已收市交易日。今次開始日前可用 64 日 → 呢個策略實際可評估嘅日子係 0。建議回填更早原生日線，或將開始日推後至 2026-06-16。"',
            '"策略 90EMA 約需 90 個交易日暖機；目前可用 64 日，實際可評估約 0 日。建議起點提前到 2026-01-15。"',
          )
          .replace("neededDays: 95,", "neededDays: 90,")
          .replace(
            'suggestedStart: "2026-06-16",',
            'suggestedStart: "2026-01-15",',
          ),
      [
        "src/lib/backtest/backtest.test.ts",
        "-t",
        "exact 95-day boundary",
      ],
    ),
  ],
  [
    "guard copy reverted to standard run jargon",
    mutate(
      "guard copy reverted to standard run jargon",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source.replace(
          "return `${count} 次回測用緊呢個版本`;",
          "return `${count} 個 standard run 用緊`;",
        ),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "version library ban-scan",
      ],
    ),
  ],
  [
    "strategy-name display sanitization removed",
    mutate(
      "strategy-name display sanitization removed",
      "src/components/strategies/LibraryTab.tsx",
      (source) =>
        source
          .replace("displayStrategyName(version.name)", "version.name")
          .replace("displayStrategyName(selected.name)", "selected.name"),
      [
        "src/pages/StrategiesPage.test.tsx",
        "-t",
        "version library ban-scan",
      ],
    ),
  ],
];

console.log("\n=== P2 VERSION LIFECYCLE MUTATION SUMMARY ===");
for (const [name, red] of results) {
  console.log(
    `${name}: ${red === null ? "SKIPPED" : red ? "RED ok" : "DID NOT FAIL"}`,
  );
}
process.exit(results.some(([, red]) => red === false) ? 1 : 0);
