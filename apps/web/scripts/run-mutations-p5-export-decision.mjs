import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

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
    name: "export status/ok guard",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects 404 before download success",
    edits: (eol) => [
      {
        before: lines(eol, [
          "    input.status === 200 && input.ok === true,",
          '    "result-export",',
        ]),
        after: lines(eol, [
          '    typeof input.status === "number",',
          '    "result-export",',
        ]),
      },
    ],
  },
  {
    name: "export content-type guard",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong content type before download success",
    edits: () => [
      {
        before: 'input.contentType === "application/zip"',
        after: 'typeof input.contentType === "string"',
      },
    ],
  },
  {
    name: "export content-disposition filename guard",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong filename before download success",
    edits: (eol) => [
      {
        before: lines(eol, [
          "      envelope.contentDisposition ===",
          '        `attachment; filename="${fileName}"`,',
        ]),
        after: '      typeof envelope.contentDisposition === "string",',
      },
    ],
  },
  {
    name: "ZIP exact member set/order",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong order, extra member and sidecar identity/ref drift",
    edits: (eol) => [
      {
        before: lines(eol, [
          "      actualMembers.length === members.length &&",
          "        actualMembers.every((name, index) => name === members[index]),",
        ]),
        after: "      actualMembers.length === members.length,",
      },
    ],
  },
  {
    name: "ZIP member run/ref reconciliation",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects wrong order, extra member and sidecar identity/ref drift",
    edits: (eol) => [
      {
        before: lines(eol, [
          "      exportedMain.refs.trades === members[1] &&",
          "        exportedMain.refs.equity === members[2] &&",
          "        exportedMain.refs.events === members[3],",
        ]),
        after: lines(eol, [
          "      exportedMain.refs.trades.length > 0 &&",
          "        exportedMain.refs.equity.length > 0 &&",
          "        exportedMain.refs.events.length > 0,",
        ]),
      },
    ],
  },
  {
    name: "Blob created before complete validation",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "creates no Blob URL for malformed success",
    edits: (eol) => [
      {
        before:
          "        const verified = await parseP5ResultExport(response, main);",
        after: lines(eol, [
          "        URL.createObjectURL(",
          "          new Blob([response.arrayBuffer], { type: \"application/zip\" }),",
          "        );",
          "        const verified = await parseP5ResultExport(response, main);",
        ]),
      },
    ],
  },
  {
    name: "decision reason trim gate",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "sends exact trimmed return request",
    edits: (eol) => [
      {
        before: lines(eol, [
          "        decision: type,",
          "        reason: trimmedReason,",
        ]),
        after: lines(eol, [
          "        decision: type,",
          "        reason,",
        ]),
      },
    ],
  },
  {
    name: "return token changed to rework",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "sends exact trimmed return request",
    edits: () => [
      {
        before: "        decision: type,",
        after:
          '        decision: type === "return" ? ("rework" as DecisionType) : type,',
      },
    ],
  },
  {
    name: "request adds client-derived provenance",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "sends exact trimmed return request",
    edits: (eol) => [
      {
        before: lines(eol, [
          "        decision: type,",
          "        reason: trimmedReason,",
        ]),
        after: lines(eol, [
          "        decision: type,",
          "        reason: trimmedReason,",
          "        ...({ strategy_version: normalMain.strategyVersion } as Record<",
          "          string,",
          "          string",
          "        >),",
        ]),
      },
    ],
  },
  {
    name: "unknown retry regenerates request id",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "retries the byte-exact request once",
    edits: (eol) => [
      {
        before: lines(eol, [
          '    if (!snapshot || decisionPhase !== "unknown") {',
          "      return;",
          "    }",
          "    void sendNormalDecision(snapshot);",
        ]),
        after: lines(eol, [
          '    if (!snapshot || decisionPhase !== "unknown") {',
          "      return;",
          "    }",
          "    snapshot.request.request_id = crypto.randomUUID();",
          "    void sendNormalDecision(snapshot);",
        ]),
      },
    ],
  },
  {
    name: "network unknown mislabeled confirmed no-write",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "retries the byte-exact request once",
    edits: () => [
      {
        before:
          "結果未知：伺服器可能已記錄。理由同 request id 已凍結，只可用同一請求重試。",
        after:
          "確定未寫入：伺服器沒有記錄。理由同 request id 已凍結，只可用同一請求重試。",
      },
    ],
  },
  {
    name: "history append before strict POST 200",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "appends zero records for 503 decision response",
    edits: (eol) => [
      {
        before: lines(eol, [
          "      const parsed = parseP5PromotionDecisionRecord(",
          "        response,",
        ]),
        after: lines(eol, [
          "      setDecisions((previous) => [",
          "        ...previous,",
          "        {",
          '          id: "premature",',
          "          requestId: snapshot.request.request_id,",
          "          type: snapshot.request.decision,",
          "          runId: snapshot.runId,",
          "          strategyVersion: snapshot.main.strategyVersion,",
          "          reason: snapshot.request.reason,",
          "          scorecardSnapshot: snapshot.main.scorecard,",
          '          at: "premature",',
          "        },",
          "      ]);",
          "      const parsed = parseP5PromotionDecisionRecord(",
          "        response,",
        ]),
      },
    ],
  },
  {
    name: "history run/hash/scorecard identity guards",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "rejects count/run/order/id/hash/strategy/scorecard drift",
    edits: () => [
      {
        before: "object.run_id === main.runId",
        after: "nonBlankExactString(object.run_id)",
      },
      {
        before:
          "strategy.content_sha256 === binding.content_sha256",
        after: "lowercaseSha256(strategy.content_sha256)",
      },
      {
        before:
          "jsonEqual(object.scorecard_snapshot, main.rawScorecard)",
        after: "Array.isArray(object.scorecard_snapshot)",
      },
    ],
  },
  {
    name: "late history overwrites newer POST",
    file: "src/lib/results/normalContract.ts",
    testFile: CONTRACT_TEST,
    testName: "guards late history from overwriting a newer POST revision",
    edits: () => [
      {
        before: "  return revisionAtStart === currentRevision;",
        after: "  return true;",
      },
    ],
  },
  {
    name: "owner-review leaks normal history request",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "isolated from all three normal endpoints",
    edits: (eol) => [
      {
        before: lines(eol, [
          "    if (isReview) {",
          "      const fixture = getFixtureDetail(runId);",
        ]),
        after: lines(eol, [
          "    if (isReview) {",
          "      void fetchP5RunPromotionDecisions(runId, controller.signal);",
          "      const fixture = getFixtureDetail(runId);",
        ]),
      },
    ],
  },
  {
    name: "decision route/mode/generation guard",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "drops a late POST after switching to owner-review",
    edits: (eol) => [
      {
        before: lines(eol, [
          "  const decisionSnapshotCurrent = (",
          "    snapshot: NormalDecisionSnapshot,",
          "  ): boolean =>",
          "    mountedRef.current &&",
          "    isP5GenerationCurrent(",
          "      generationRef.current,",
          "      snapshot.generation,",
          "    ) &&",
          "    identityRef.current.mode === snapshot.mode &&",
          "    identityRef.current.runId === snapshot.runId;",
        ]),
        after: lines(eol, [
          "  const decisionSnapshotCurrent = (",
          "    _snapshot: NormalDecisionSnapshot,",
          "  ): boolean => true;",
        ]),
      },
      {
        before: lines(eol, [
          "    exportInFlightRef.current = null;",
          "    decisionSnapshotRef.current = null;",
          "    historyRevisionRef.current = 0;",
        ]),
        after: lines(eol, [
          "    exportInFlightRef.current = null;",
          "    historyRevisionRef.current = 0;",
        ]),
      },
    ],
  },
  {
    name: "use success opens P6",
    file: "src/pages/RunDetailPage.tsx",
    testFile: PAGE_TEST,
    testName: "never opens P6",
    edits: (eol) => [
      {
        before: lines(eol, [
          "      decisionSnapshotRef.current = null;",
          "      setDecisionNote(",
        ]),
        after: lines(eol, [
          "      decisionSnapshotRef.current = null;",
          '      if (record.type === "use") {',
          '        window.history.pushState(null, "", "/paper");',
          "      }",
          "      setDecisionNote(",
        ]),
      },
    ],
  },
];

console.log(
  `P5 export/decision mutations: ${mutations.length} sequential cases`,
);

for (const [index, mutation] of mutations.entries()) {
  const path = join(WEB_ROOT, mutation.file);
  const original = readFileSync(path);
  const baseline = sha256(original);
  const source = original.toString("utf8");
  const eol = source.includes("\r\n") ? "\r\n" : "\n";
  const edits = mutation.edits(eol);
  let mutated = source;

  for (const { before, after } of edits) {
    const count = occurrences(mutated, before);
    if (count !== 1) {
      throw new Error(
        `${mutation.name}: expected one exact mutation site, found ${count}`,
      );
    }
    mutated = mutated.replace(before, after);
  }

  let redProblem = null;
  let redStatus = null;
  try {
    writeFileSync(path, mutated, "utf8");
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
    `[${String(index + 1).padStart(2, "0")}/17] ${mutation.name}: ` +
      `RED(${redStatus}) -> restore ${restored.slice(0, 12)} -> GREEN`,
  );
}

console.log(
  "P5 export/decision mutations: 17/17 RED -> restore -> GREEN",
);
