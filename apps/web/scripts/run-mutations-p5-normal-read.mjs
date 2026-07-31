import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

const WEB_ROOT = fileURLToPath(new URL("../", import.meta.url));
const VITEST = join(WEB_ROOT, "node_modules", "vitest", "vitest.mjs");
const CONTRACT_TEST = "src/lib/results/normalContract.test.ts";
const PAGE_TEST = "src/pages/ResultsPage.test.tsx";

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function occurrences(source, needle) {
  let count = 0;
  let offset = 0;
  while ((offset = source.indexOf(needle, offset)) !== -1) {
    count += 1;
    offset += needle.length;
  }
  return count;
}

function lines(eol, values) {
  return values.join(eol);
}

function runDesignatedTest(testFile, testName) {
  return spawnSync(
    process.execPath,
    [
      VITEST,
      "run",
      "--pool=threads",
      "--no-file-parallelism",
      "--maxWorkers=2",
      testFile,
      "-t",
      testName,
    ],
    {
      cwd: WEB_ROOT,
      encoding: "utf8",
      timeout: 120_000,
      windowsHide: true,
    },
  );
}

function outputTail(result) {
  return `${result.stdout ?? ""}\n${result.stderr ?? ""}`.slice(-6_000);
}

const mutations = [
  {
    name: "list schema check",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong schema",
    replace: () => ({
      before: 'body.schema === "run_list.v1"',
      after: 'typeof body.schema === "string"',
    }),
  },
  {
    name: "count reconciliation",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong count",
    replace: () => ({
      before:
        "nonNegativeInteger(body.count) && body.count === body.runs.length",
      after: "nonNegativeInteger(body.count)",
    }),
  },
  {
    name: "exact-false validation filter",
    file: "src/pages/ResultsPage.tsx",
    testFile: PAGE_TEST,
    testName: "hides validation_run true",
    replace: () => ({
      before: "run.validation_run === false",
      after: "run.validation_run",
    }),
  },
  {
    name: "detail expected run",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects route/main/manifest identity mismatch",
    replace: () => ({
      before:
        "nonBlankExactString(run.run_id) && run.run_id === expectedRunId",
      after: "nonBlankExactString(run.run_id)",
    }),
  },
  {
    name: "chart requested timeframe",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong run/timeframe/contract",
    replace: () => ({
      before: "body.timeframe === expectedTimeframe",
      after: 'typeof body.timeframe === "string"',
    }),
  },
  {
    name: "generation guard",
    file: "src/lib/results/normalContract.ts",
    testFile: PAGE_TEST,
    testName: "rejects stale generation tokens",
    replace: () => ({
      before: "return currentGeneration === expectedGeneration;",
      after: "return true;",
    }),
  },
  {
    name: "unavailable versus known-empty",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "keeps legacy unavailable distinct",
    replace: () => ({
      before: 'return { kind: "unavailable", trades: [] };',
      after: 'return { kind: "complete", trades: [] };',
    }),
  },
  {
    name: "ranking layer depth",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "applies every structural comparator tie level",
    replace: () => ({
      before: "const depth = rejectionDepth(b) - rejectionDepth(a);",
      after: "const depth = 0;",
    }),
  },
  {
    name: "numeric-distance ranking prohibited",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "never numeric actual-required distance",
    replace: (eol) => ({
      before: lines(eol, [
        "    .sort((a, b) => {",
        "      const depth = rejectionDepth(b) - rejectionDepth(a);",
      ]),
      after: lines(eol, [
        "    .sort((a, b) => {",
        "      const numericDistance =",
        "        Math.abs(",
        "          Number(a.condition_facts[0]?.actual) -",
        "            Number(a.condition_facts[0]?.required),",
        "        ) -",
        "        Math.abs(",
        "          Number(b.condition_facts[0]?.actual) -",
        "            Number(b.condition_facts[0]?.required),",
        "        );",
        "      if (numericDistance !== 0) return numericDistance;",
        "      const depth = rejectionDepth(b) - rejectionDepth(a);",
      ]),
    }),
  },
  {
    name: "catalog failure isolation",
    file: "src/pages/ResultsPage.tsx",
    testFile: PAGE_TEST,
    testName: "catalog 503",
    replace: (eol) => ({
      before: lines(eol, [
        "        if (active && !controller.signal.aborted && parsed.ok) {",
        "          setStrategyLabels(parsed.value);",
        "        }",
      ]),
      after: lines(eol, [
        "        if (active && !controller.signal.aborted && !parsed.ok) {",
        "          setError(parsed.error);",
        '          setLoad("error");',
        "          return;",
        "        }",
        "        if (active && !controller.signal.aborted && parsed.ok) {",
        "          setStrategyLabels(parsed.value);",
        "        }",
      ]),
    }),
  },
  {
    name: "verified main before mark-read",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "invalid or validation main is fatal",
    replace: (eol) => ({
      before: lines(eol, [
        "        const parsed = parseP5ResultMain(response, runId);",
        "        if (!parsed.ok) {",
      ]),
      after: lines(eol, [
        "        markRunRead(runId, mode);",
        "        const parsed = parseP5ResultMain(response, runId);",
        "        if (!parsed.ok) {",
      ]),
    }),
  },
  {
    name: "30m no-copy",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "verifies main before mark-read",
    replace: (eol) => ({
      before: lines(eol, [
        '      timeframe: "30m",',
        '      roleLabel: "輔助",',
        "      available: false,",
        "      candles: [],",
      ]),
      after: lines(eol, [
        '      timeframe: "30m",',
        '      roleLabel: "輔助",',
        '      available: chartChildren["1H"].data?.available ?? false,',
        '      candles: chartChildren["1H"].data?.candles ?? [],',
      ]),
    }),
  },
  {
    name: "chart cache required closed enum",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects chart cache bypass",
    replace: (eol) => ({
      before: lines(eol, [
        '      typeof body.cache === "string" &&',
        "        (P5_CHART_CACHE_STATES as readonly string[]).includes(body.cache),",
      ]),
      after: '      typeof body.cache === "string",',
    }),
  },
];

console.log(`P5 normal-read mutations: ${mutations.length} sequential cases`);

for (const [index, mutation] of mutations.entries()) {
  const path = join(WEB_ROOT, mutation.file);
  const original = readFileSync(path);
  const baseline = sha256(original);
  const source = original.toString("utf8");
  const eol = source.includes("\r\n") ? "\r\n" : "\n";
  const { before, after } = mutation.replace(eol);
  const count = occurrences(source, before);
  if (count !== 1) {
    throw new Error(
      `${mutation.name}: expected one exact mutation site, found ${count}`,
    );
  }

  let redProblem = null;
  let redStatus = null;
  try {
    writeFileSync(path, source.replace(before, after), "utf8");
    const red = runDesignatedTest(mutation.testFile, mutation.testName);
    redStatus = red.status;
    if (red.error) {
      redProblem = new Error(
        `${mutation.name}: RED command error ${red.error.message}\n${outputTail(red)}`,
      );
    } else if (red.status === 0) {
      redProblem = new Error(
        `${mutation.name}: mutant survived designated test\n${outputTail(red)}`,
      );
    }
  } catch (error) {
    redProblem =
      error instanceof Error ? error : new Error(String(error));
  } finally {
    writeFileSync(path, original);
  }

  const restored = sha256(readFileSync(path));
  if (restored !== baseline) {
    throw new Error(
      `${mutation.name}: restore SHA mismatch ${baseline} != ${restored}`,
    );
  }

  const green = runDesignatedTest(mutation.testFile, mutation.testName);
  if (green.error || green.status !== 0) {
    throw new Error(
      `${mutation.name}: restored test did not GREEN\n${outputTail(green)}`,
    );
  }
  if (redProblem) {
    throw redProblem;
  }

  console.log(
    `[${String(index + 1).padStart(2, "0")}/13] ${mutation.name}: ` +
      `RED(${redStatus}) → restore ${restored.slice(0, 12)} → GREEN`,
  );
}

console.log("P5 normal-read mutations: 13/13 RED → restore → GREEN");
