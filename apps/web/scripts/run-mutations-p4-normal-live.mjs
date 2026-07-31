/**
 * Required mutation evidence for the P4 normal live seam.
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
  console.log(
    `${red ? "RED" : "GREEN(!)"} ${name} exit=${result.code}`,
  );
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
    "full capital divided across matrix",
    mutate(
      "full capital divided across matrix",
      "src/lib/backtest/liveContract.ts",
      (source) =>
        source.replace(
          "initial_capital_usd: input.assumptions.initialCapital,",
          "initial_capital_usd: input.assumptions.initialCapital / (input.strategyVersions.length * input.symbols.length),",
        ),
      [
        "src/lib/backtest/liveContract.test.ts",
        "-t",
        "serializes only the D1 envelope",
      ],
    ),
  ],
  [
    "precheck late-response identity gate removed",
    mutate(
      "precheck late-response identity gate removed",
      "src/lib/backtest/useP4Live.ts",
      (source) =>
        source.replace(
          `if (
          !mountedRef.current ||
          sequence !== precheckSequenceRef.current
        ) {`,
          `if (
          !mountedRef.current
        ) {`,
        ),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "late A cannot replace B",
      ],
    ),
  ],
  [
    "malformed precheck extra field accepted",
    mutate(
      "malformed precheck extra field accepted",
      "src/lib/backtest/liveContract.ts",
      (source) =>
        source.replace(
          "if (!isObject(value) || !hasExactKeys(value, PRECHECK_KEYS)) {",
          "if (!isObject(value)) {",
        ),
      [
        "src/lib/backtest/liveContract.test.ts",
        "-t",
        "fails closed on extra top key",
      ],
    ),
  ],
  [
    "submit 409 treated as ordinary success path",
    mutate(
      "submit 409 treated as ordinary success path",
      "src/lib/backtest/useP4Live.ts",
      (source) =>
        source.replace(
          "if (response.status === 409) {",
          "if (false && response.status === 409) {",
        ),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "submit 409 updates precheck truth",
      ],
    ),
  ],
  [
    "cancel response may cancel running cell",
    mutate(
      "cancel response may cancel running cell",
      "src/lib/backtest/liveContract.ts",
      (source) =>
        source
          .replace(
            'running: new Set(["running", "completed", "failed"]),',
            'running: new Set(["running", "completed", "failed", "cancelled"]),',
          )
          .replace(
            'if (source === "cancel") {',
            'if (false && source === "cancel") {',
          )
          .replace(
            "(oldJob.progress !== null && newJob.progress === null) ||",
            "false ||",
          ),
      [
        "src/lib/backtest/liveContract.test.ts",
        "-t",
        "never touches running",
      ],
    ),
  ],
  [
    "five-state summary omits cancelled",
    mutate(
      "five-state summary omits cancelled",
      "src/lib/backtest/progress.ts",
      (source) =>
        source.replace(
          "` · 失敗 ${c.failed} · 已取消 ${c.cancelled} · 共 ${c.total} 次`",
          "` · 失敗 ${c.failed} · 共 ${c.total} 次`",
        ),
      [
        "src/lib/backtest/backtest.test.ts",
        "-t",
        "progress five buckets",
      ],
    ),
  ],
  [
    "history falls back to validation flag heuristic",
    mutate(
      "history falls back to validation flag heuristic",
      "src/lib/backtest/useP4Live.ts",
      (source) =>
        source.replace(
          "(batch) => parseP4StandardRequest(batch.request) !== null,",
          '(batch) => batch.request.validation_run === false,',
        ),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "history only lists exact standard snapshots",
      ],
    ),
  ],
  [
    "rerun carries old duplicate acknowledgement",
    mutate(
      "rerun carries old duplicate acknowledgement",
      "src/pages/BacktestPage.tsx",
        (source) =>
          source.replace(
          `setLiveAcknowledgements(null);
              setRerunSymbolAuthorization({`,
          `setLiveAcknowledgements({
                formKey: p4FormIdentity(request),
                items: [...request.duplicate_acknowledgements],
              });
              setRerunSymbolAuthorization({`,
        ),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "rerun restores without submit or ack",
      ],
    ),
  ],
  [
    "clipboard uses summary with UI prefix",
    mutate(
      "clipboard uses summary with UI prefix",
      "src/pages/BacktestPage.tsx",
      (source) =>
        source.replace(
          "void copyExactText(job.error_full!).then(",
          'void copyExactText(`原因：${job.error_summary}`).then(',
        ),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "full error, and exact clipboard",
      ],
    ),
  ],
  [
    "owner-review accidentally enables live POST",
    mutate(
      "owner-review accidentally enables live POST",
      "src/pages/BacktestPage.tsx",
      (source) =>
        source.replace("enabled: !isReview,", "enabled: true,"),
      [
        "src/pages/BacktestPage.test.tsx",
        "-t",
        "is zero-fetch",
      ],
    ),
  ],
];

console.log("\n=== P4 NORMAL LIVE MUTATION SUMMARY ===");
for (const [name, red] of results) {
  console.log(
    `${name}: ${red === null ? "SKIPPED" : red ? "RED ok" : "DID NOT FAIL"}`,
  );
}
process.exit(results.some(([, red]) => red === false) ? 1 : 0);
