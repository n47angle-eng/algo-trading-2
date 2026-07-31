import { createHash } from "node:crypto";
import {
  readFileSync,
  writeFileSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const vitestCli = path.join(webRoot, "node_modules", "vitest", "vitest.mjs");
const componentFile = "src/components/strategies/InsightTab.tsx";
const contractFile = "src/lib/insights/liveContract.ts";
const clientFile = "src/api/client.ts";
const mountedTest = "src/lib/p2/insightArchive.test.tsx";
const contractTest = "src/lib/insights/liveContract.test.ts";

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function replaceExact(source, before, after, id) {
  const first = source.indexOf(before);
  if (first < 0) {
    throw new Error(`${id}: exact mutation anchor was not found`);
  }
  return source.slice(0, first) + after + source.slice(first + before.length);
}

function runDesignated(testFile, testName) {
  return spawnSync(
    process.execPath,
    [
      vitestCli,
      "run",
      testFile,
      "-t",
      testName,
      "--maxWorkers=1",
    ],
    {
      cwd: webRoot,
      encoding: "utf8",
      timeout: 120_000,
      windowsHide: true,
    },
  );
}

function commandOutput(result) {
  return `${result.stdout ?? ""}\n${result.stderr ?? ""}`.trim();
}

function withoutAnsi(value) {
  return value.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "");
}

const mutations = [
  {
    id: "M01 normal active list uses local insight store",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "normal never reads/writes the old insight store and status is immutable/read-only",
    before: `      if (!isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
        return;
      }
      const revision = listRevisionRef.current + 1;`,
    after: `      if (!isLiveSnapshotCurrent(generation, expectedFixtureEpoch)) {
        return;
      }
      void listInsights(storage);
      const revision = listRevisionRef.current + 1;`,
  },
  {
    id: "M02 normal import uses legacy importInsight",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "HTTP 422 keeps exact textarea, writes no local insight and never retries",
    before: `    const candidateSnapshot = {
      origin: candidate.origin,
      insight_id: candidate.insight_id,
      version: candidate.version,
    };
    invalidateLiveLists();`,
    after: `    const candidateSnapshot = {
      origin: candidate.origin,
      insight_id: candidate.insight_id,
      version: candidate.version,
    };
    void importInsight(sourceSnapshot, storage, context);
    invalidateLiveLists();`,
  },
  {
    id: "M03 normal status uses legacy updateInsightStatus",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "normal never reads/writes the old insight store and status is immutable/read-only",
    before: `                            <span>{item.validation_status}</span>`,
    after: `                            <span>
                              {(
                                updateInsightStatus(
                                  item.insight_id,
                                  item.latest_version,
                                  item.validation_status,
                                  storage,
                                  item.origin,
                                ),
                                item.validation_status
                              )}
                            </span>`,
  },
  {
    id: "M04 normal archive uses legacy deleteInsight",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "grace sends DELETE 0; undo keeps DELETE at 0 forever and old delete helper at 0",
    before: `  const beginLiveArchive = (summary: LiveInsightSummary) => {
    if (ownerReview || liveModeRef.current !== "normal") {
      return;
    }
    const key = insightIdentityKey(summary.origin, summary.insight_id);`,
    after: `  const beginLiveArchive = (summary: LiveInsightSummary) => {
    if (ownerReview || liveModeRef.current !== "normal") {
      return;
    }
    void deleteInsight(
      summary.insight_id,
      summary.latest_version,
      storage,
      summary.origin,
    );
    const key = insightIdentityKey(summary.origin, summary.insight_id);`,
  },
  {
    id: "M05 archive grace becomes immediate DELETE",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "grace sends DELETE 0; undo keeps DELETE at 0 forever and old delete helper at 0",
    before: `    const timer = window.setTimeout(() => {
      void executeLiveArchive(key, snapshot);
    }, INSIGHT_ARCHIVE_GRACE_MS);`,
    after: `    const timer = window.setTimeout(() => {
      void executeLiveArchive(key, snapshot);
    }, 0);`,
  },
  {
    id: "M06 undo leaks a DELETE",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "grace sends DELETE 0; undo keeps DELETE at 0 forever and old delete helper at 0",
    before: `    if (action?.stage !== "grace") {
      return;
    }
    clearArchiveAction(key);`,
    after: `    if (action?.stage !== "grace") {
      return;
    }
    void archiveLiveInsight(
      action.snapshot.origin,
      action.snapshot.insight_id,
    );
    clearArchiveAction(key);`,
  },
  {
    id: "M07 unmount cleanup removed",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "navigation/unmount cancels grace instead of flushing a destructive request",
    before: `    return () => {
      if (liveGenerationRef.current === generation) {
        mountedRef.current = false;
      }
      cancelAllLiveWork();
    };`,
    after: `    return () => {};`,
  },
  {
    id: "M08 archive failure retries DELETE",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "malformed archive failure restores the row, rereads both lists once and never retries",
    before: `      const parsed = parseStrictHttp<LiveInsightArchive>(result, () =>
        parseInsightArchive(result.body, snapshot),
      );
      if (!parsed.ok) {
        clearArchiveAction(key);`,
    after: `      const parsed = parseStrictHttp<LiveInsightArchive>(result, () =>
        parseInsightArchive(result.body, snapshot),
      );
      if (!parsed.ok) {
        void archiveLiveInsight(snapshot.origin, snapshot.insight_id);
        clearArchiveAction(key);`,
  },
  {
    id: "M09 archive identity guard removed",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "malformed archive failure restores the row, rereads both lists once and never retries",
    before: `        parseInsightArchive(result.body, snapshot),`,
    after: `        parseInsightArchive(result.body, {
          ...snapshot,
          origin: (result.body as LiveInsightArchive).origin,
        }),`,
  },
  {
    id: "M10 restore identity guard removed",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "malformed restore failure retains archive row, rereads both lists once and never retries",
    before: `          parseInsightRestore(result.body, snapshot),`,
    after: `          parseInsightRestore(result.body, {
            ...snapshot,
            archive_id: (result.body as LiveInsightRestore).archive_id,
          }),`,
  },
  {
    id: "M11 late active-list revision guard removed",
    file: componentFile,
    testFile: mountedTest,
    testName: "older in-flight list cannot overwrite",
    before: `            listRevisionRef.current !== revision ||
            activeListControllerRef.current !== activeController`,
    after: `            false`,
  },
  {
    id: "M12 owner-review leaks a live request",
    file: componentFile,
    testFile: mountedTest,
    testName:
      "keeps fixture import/status/detail/delete and sends all six live endpoint counts to zero",
    before: `    if (ownerReview) {
      setActiveResource({`,
    after: `    if (ownerReview) {
      void fetchLiveInsights();
      setActiveResource({`,
  },
  {
    id: "M13 composite key drops origin",
    file: contractFile,
    testFile: contractTest,
    testName: "keeps the same insight id distinct across origins",
    before: `  return \`\${origin}\\u0000\${insightId}\`;`,
    after: `  return insightId;`,
  },
  {
    id: "M14 restore sends an empty JSON body",
    file: clientFile,
    testFile: contractTest,
    testName:
      "uses exact paths/methods, encode-once segments, exact import body and no restore body",
    before: `    )}/restore\`,
    "POST",
    undefined,
    signal,
  );`,
    after: `    )}/restore\`,
    "POST",
    {} as { source_text: string },
    signal,
  );`,
  },
  {
    id: "M15 UUID version and variant guard removed",
    file: contractFile,
    testFile: contractTest,
    testName:
      "rejects non-v4 archive ids across archive, list and restore adapters",
    before: `const ARCHIVE_ID =
  /^delete-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/;`,
    after: `const ARCHIVE_ID = /^delete-[0-9a-f]{32}$/;`,
  },
  {
    id: "M16 archive list dedupe regresses to a three-column tuple",
    file: contractFile,
    testFile: contractTest,
    testName:
      "rejects the same composite identity with different archive ids",
    before: `    const compositeKey = insightIdentityKey(
      parsed.origin,
      parsed.insight_id,
    );`,
    after: `    const compositeKey = insightArchiveIdentityKey(
      parsed.origin,
      parsed.insight_id,
      parsed.archive_id,
    );`,
  },
];

const requestedMutation = process.env.INSIGHT_MUTATION;
const selectedMutations = requestedMutation
  ? mutations.filter((mutation) => mutation.id.startsWith(requestedMutation))
  : mutations;
if (selectedMutations.length === 0) {
  throw new Error(`unknown INSIGHT_MUTATION prefix: ${requestedMutation}`);
}

for (const mutation of selectedMutations) {
  const absoluteFile = path.join(webRoot, mutation.file);
  const baseline = readFileSync(absoluteFile);
  const baselineSha = sha256(baseline);
  const mutated = replaceExact(
    baseline.toString("utf8"),
    mutation.before,
    mutation.after,
    mutation.id,
  );

  let redResult;
  try {
    writeFileSync(absoluteFile, mutated, "utf8");
    redResult = runDesignated(mutation.testFile, mutation.testName);
    if (redResult.error) {
      throw redResult.error;
    }
    if (redResult.status === 0) {
      throw new Error(`${mutation.id}: mutant survived its designated test`);
    }
    if (!/\b1 failed\b/.test(withoutAnsi(commandOutput(redResult)))) {
      throw new Error(
        `${mutation.id}: RED did not identify exactly one failed designated test\n` +
          commandOutput(redResult),
      );
    }
  } finally {
    writeFileSync(absoluteFile, baseline);
    const restoredSha = sha256(readFileSync(absoluteFile));
    if (restoredSha !== baselineSha) {
      throw new Error(
        `${mutation.id}: byte restore SHA mismatch ${restoredSha} != ${baselineSha}`,
      );
    }
  }

  const greenResult = runDesignated(mutation.testFile, mutation.testName);
  if (
    greenResult.error ||
    greenResult.status !== 0 ||
    !/\b1 passed\b/.test(withoutAnsi(commandOutput(greenResult)))
  ) {
    const details = commandOutput(greenResult);
    throw new Error(
      `${mutation.id}: restored designated test was not GREEN\n${details}`,
    );
  }
  console.log(
    `PASS ${mutation.id}: RED -> byte restore ${baselineSha.slice(0, 12)} -> GREEN`,
  );
}

console.log(
  `PASS all ${selectedMutations.length} selected insight archive mutations`,
);
