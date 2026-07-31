/**
 * Required mutation evidence for the P2 shared catalog request ([181]).
 * Every temporary edit is restored byte-for-byte before the next mutation.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const requested = process.argv.slice(2);

function runVitest(testName) {
  const result = spawnSync(
    "npx",
    [
      "vitest",
      "run",
      "src/lib/p2/sharedCatalog.test.tsx",
      "-t",
      testName,
      "--reporter=dot",
    ],
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

function mutate(name, file, transform, testName) {
  if (
    requested.length > 0 &&
    !requested.some((fragment) => name.includes(fragment))
  ) {
    return null;
  }
  const path = resolve(root, file);
  const original = readFileSync(path);
  const source = original.toString("utf8");
  const changed = transform(source);
  if (changed === source) {
    console.log(`SKIP ${name} (target not found)`);
    return false;
  }

  let result;
  try {
    writeFileSync(path, changed, "utf8");
    result = runVitest(testName);
  } finally {
    writeFileSync(path, original);
  }
  if (!readFileSync(path).equals(original)) {
    throw new Error(`${name}: source was not restored byte-for-byte`);
  }
  const red = result.code !== 0;
  console.log(`${red ? "RED" : "GREEN(!)"} ${name} exit=${result.code}`);
  if (!red) {
    console.log(
      result.output
        .split("\n")
        .filter((line) => /Tests|FAIL/.test(line))
        .slice(0, 6)
        .join("\n"),
    );
  }
  return red;
}

const results = [
  [
    "catalog query removed",
    mutate(
      "catalog query removed",
      "src/api/client.ts",
      (source) =>
        source.replace(
          'getJson<unknown>("/api/v1/data/coverage?view=catalog")',
          'getJson<unknown>("/api/v1/data/coverage")',
        ),
      "normal mounted workbench uses one exact catalog request",
    ),
  ],
  [
    "catalog fetch restored to per-tab lifecycle",
    mutate(
      "catalog fetch restored to per-tab lifecycle",
      "src/components/strategies/SketchTab.tsx",
      (source) =>
        source
          .replace(
            'import { useWorkbench } from "../../lib/p2/WorkbenchContext";',
            'import { useInstrumentCatalog } from "../../lib/catalog/useInstrumentCatalog";\nimport { useWorkbench } from "../../lib/p2/WorkbenchContext";',
          )
          .replace(
            "  const { storage, catalog, ownerReview } = useWorkbench();",
            "  const { storage, ownerReview } = useWorkbench();\n  const catalog = useInstrumentCatalog(!ownerReview);",
          ),
      "normal mounted workbench uses one exact catalog request",
    ),
  ],
  [
    "owner-review zero-fetch guard removed",
    mutate(
      "owner-review zero-fetch guard removed",
      "src/lib/p2/WorkbenchContext.tsx",
      (source) =>
        source.replace(
          "const liveCatalog = useInstrumentCatalog(!ownerReview);",
          "const liveCatalog = useInstrumentCatalog(true);",
        ),
      "owner-review keeps coverage requests at zero",
    ),
  ],
  [
    "invalid catalog falls back to owner fixture",
    mutate(
      "invalid catalog falls back to owner fixture",
      "src/lib/p2/WorkbenchContext.tsx",
      (source) =>
        source.replace(
          `  const catalog = ownerReview
    ? (fixtureCatalog ?? ownerReviewCatalogState("ready"))
    : liveCatalog;`,
          `  const catalog = ownerReview
    ? (fixtureCatalog ?? ownerReviewCatalogState("ready"))
    : liveCatalog.status === "ready"
      ? liveCatalog
      : ownerReviewCatalogState("ready");`,
        ),
      "fail-closed invalid catalog state",
    ),
  ],
];

console.log("\n=== P2 SHARED CATALOG MUTATION SUMMARY ===");
for (const [name, result] of results) {
  console.log(`${name}: ${result === null ? "SKIPPED" : result ? "RED ok" : "FAILED"}`);
}
const executed = results.filter(([, result]) => result !== null);
if (executed.length === 0 || executed.some(([, result]) => result !== true)) {
  process.exitCode = 1;
}
