/**
 * Mutations for P2 Normal Live Seam A + Correction A/B.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

function runVitest(args) {
  const r = spawnSync("npx", ["vitest", "run", ...args, "--reporter=dot"], {
    cwd: root,
    encoding: "utf8",
    shell: true,
  });
  return { code: r.status ?? 1, out: (r.stdout || "") + (r.stderr || "") };
}

function mut(name, file, transform, vitestArgs) {
  const path = resolve(root, file);
  const orig = readFileSync(path, "utf8");
  const next = transform(orig);
  if (next === orig) {
    console.log(`SKIP ${name}`);
    return false;
  }
  writeFileSync(path, next);
  const { code } = runVitest(vitestArgs);
  writeFileSync(path, orig);
  const red = code !== 0;
  console.log(`${red ? "RED " : "GREEN(!) "}${name} exit=${code}`);
  return red;
}

const results = [];

// M1: commit before POST
results.push([
  "M_precommit",
  mut(
    "commit before POST",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        "const zipBlob = await buildSketchZipBlob(packagePreview);\n      const expected = expectedFromPackage(packagePreview, snapshot);\n      await postSketchZip(zipBlob, expected);",
        `// MUTATION: local export before POST
      commitLocalSketchExport(snapshot, packagePreview, storage);
      const zipBlob = await buildSketchZipBlob(packagePreview);
      const expected = expectedFromPackage(packagePreview, snapshot);
      await postSketchZip(zipBlob, expected);`,
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "422 leaves"],
  ),
]);

// M2: 409 as success
results.push([
  "M_409",
  mut(
    "409 as success",
    "src/lib/sketch/postSketchPackage.ts",
    (s) =>
      s.replace(
        "if (response.status !== 201) {\n    throw new SketchPostError(\n      response.status,\n      humanizeSketchPostFailure(response.status, text),\n      text,\n    );\n  }",
        "if (response.status !== 201 && response.status !== 409) {\n    throw new SketchPostError(\n      response.status,\n      humanizeSketchPostFailure(response.status, text),\n      text,\n    );\n  }\n  if (response.status === 409) {\n    return { schema: \"sketch_detail.v1\" } as never;\n  }",
      ),
    ["src/lib/sketch/postSketchPackage.test.ts", "-t", "409 is not"],
  ),
]);

// M3: remove identity gate
results.push([
  "M_identity",
  mut(
    "skip identity assert",
    "src/lib/sketch/postSketchPackage.ts",
    (s) =>
      s.replace(
        "assertSketchPostMatchesPackage(parsed, expected);\n  return parsed;",
        "return parsed;",
      ),
    ["src/lib/sketch/postSketchPackage.test.ts", "-t", "malformed 201"],
  ),
]);

// M4: late 201 only commits when A still active (old bug)
results.push([
  "M_late_active_only",
  mut(
    "exact 201 only when A active",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        "// Exact-valid 201: always commit snapshot A to store (never discard truth).\n      const viewingA = stillViewingOrigin();\n      try {\n        const result = commitLocalSketchExport(\n          snapshot,\n          packagePreview,\n          storage,\n          new Date(),\n          { preserveActiveId: !viewingA },\n        );",
        `// MUTATION: only commit when still viewing A
      const viewingA = stillViewingOrigin();
      if (!viewingA) { clearPending(); return; }
      try {
        const result = commitLocalSketchExport(
          snapshot,
          packagePreview,
          storage,
          new Date(),
          { preserveActiveId: !viewingA },
        );`,
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "late A 201"],
  ),
]);

// M5: background commit overwrites B activeId
results.push([
  "M_bg_active",
  mut(
    "background A steals activeId",
    "src/lib/sketch/store.ts",
    (s) =>
      s.replace(
        "const nextActive =\n    options?.preserveActiveId && snap.activeId\n      ? snap.activeId\n      : exported.sketchId;",
        "const nextActive = exported.sketchId; // MUT: always switch to A",
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "late A 201"],
  ),
]);

// M6: remove PNG/rationale readiness (+ cardinality so bare title/views can pass)
results.push([
  "M_png_rationale",
  mut(
    "drop PNG/rationale readiness",
    "src/lib/sketch/exportReadiness.ts",
    (s) =>
      s
        .replace(
          `// Strategy rationale required; insight may omit
  if (draft.kind === "strategy" && !draft.rationale.trim()) {
    reasons.push("整體理據未填");
  }`,
          `// MUT: no rationale gate
  if (false && draft.kind === "strategy" && !draft.rationale.trim()) {
    reasons.push("整體理據未填");
  }`,
        )
        .replace(
          "if (draft.charts.length !== 4) {\n    reasons.push(\n      `圖格數量錯誤：預期 4 格，實際 ${draft.charts.length} 格；請複製或重建草圖`,\n    );\n  }",
          "if (false && draft.charts.length !== 4) {\n    reasons.push(\n      `圖格數量錯誤：預期 4 格，實際 ${draft.charts.length} 格；請複製或重建草圖`,\n    );\n  }",
        )
        .replace(
          "if (draft.charts.length === 4) {\n    draft.charts.forEach((c, i) => {",
          "if (false && draft.charts.length === 4) {\n    draft.charts.forEach((c, i) => {",
        ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "blocks export without images"],
  ),
]);

// M7: restore timeframe-derived member names
results.push([
  "M_tf_filename",
  mut(
    "timeframe-derived filenames",
    "src/lib/sketch/store.ts",
    (s) =>
      s
        .replace(
          'import { chartSlotFileName, sketchRelativeDir } from "./paths";',
          'import { chartFileName, sketchRelativeDir } from "./paths";',
        )
        .replace(
          "const chartImages = draft.charts.map((c, slotIndex) => ({\n    fileName: chartSlotFileName(slotIndex),",
          "const chartImages = draft.charts.map((c, slotIndex) => ({\n    fileName: chartFileName(c.timeframe),",
        ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "canonical ZIP member"],
  ),
]);

// M8: drop immutable snapshot — re-prepare + commit live editor at 201
results.push([
  "M_snapshot",
  mut(
    "no immutable snapshot",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        "// Exact-valid 201: always commit snapshot A to store (never discard truth).\n      const viewingA = stillViewingOrigin();\n      try {\n        const result = commitLocalSketchExport(\n          snapshot,\n          packagePreview,\n          storage,\n          new Date(),\n          { preserveActiveId: !viewingA },\n        );",
        `// MUT: lock live editor text into package after late edits
      const viewingA = stillViewingOrigin();
      try {
        const live = activeDraftRef.current ?? snapshot;
        const livePkg = prepareSketchPackage(live, catalog);
        const result = commitLocalSketchExport(
          live,
          livePkg,
          storage,
          new Date(),
          { preserveActiveId: !viewingA },
        );`,
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "does not lock new text"],
  ),
]);

// M9: drop double-submit ref
results.push([
  "M_double",
  mut(
    "no double-submit ref",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        "if (pendingExportIdsRef.current.has(draft.sketchId)) {\n      return;\n    }\n    pendingExportIdsRef.current.add(draft.sketchId);\n    setExportBusy(true);",
        "if (false) {\n      return;\n    }\n    // MUT: no pending id lock\n    setExportBusy(true);",
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "exact one POST"],
  ),
]);

// M10: owner-review still POST
results.push([
  "M_owner_post",
  mut(
    "owner-review still POST",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        "if (ownerReview) {\n        // Isolation: no business POST; local commit only",
        "if (false && ownerReview) {\n        // MUT: fall through to POST",
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "owner-review export"],
  ),
]);

// M11 Correction B: exact-four gate degraded to < 4 only
results.push([
  "M_exact_four",
  mut(
    "cardinality only <4",
    "src/lib/sketch/exportReadiness.ts",
    (s) =>
      s.replace(
        "if (draft.charts.length !== 4) {\n    reasons.push(\n      `圖格數量錯誤：預期 4 格，實際 ${draft.charts.length} 格；請複製或重建草圖`,\n    );\n  }",
        "if (draft.charts.length < 4) {\n    reasons.push(\n      `圖格數量錯誤：預期 4 格，實際 ${draft.charts.length} 格；請複製或重建草圖`,\n    );\n  }",
      ).replace(
        "if (draft.charts.length === 4) {\n    draft.charts.forEach((c, i) => {",
        "if (draft.charts.length >= 4) {\n    draft.charts.forEach((c, i) => {",
      ),
    ["src/lib/sketch/liveExport.test.tsx", "-t", "5-chart draft"],
  ),
]);

// M12 Correction B: swallow background local-commit failure again
results.push([
  "M_bg_local_alert",
  mut(
    "swallow background local badge fail",
    "src/components/strategies/SketchTab.tsx",
    (s) =>
      s.replace(
        `} catch {
        // Backend already accepted ZIP — local badge failure must stay visible.
        if (!mountedRef.current) {
          return;
        }
        if (viewingA && seq === exportSeq.current) {
          setError(localBadgeFailMsg(false));
          setExportPreview(null);
        } else {
          // Viewing B / new draft: keep B state; independent background alert.
          setBackgroundAlert(localBadgeFailMsg(true));
        }
      }`,
        `} catch {
        // MUT: only show when still viewing A (swallow background)
        if (viewingA && seq === exportSeq.current) {
          setError(localBadgeFailMsg(false));
          setExportPreview(null);
        }
      }`,
      ),
    [
      "src/lib/sketch/liveExport.test.tsx",
      "-t",
      "local commit throw: B intact, background alert",
    ],
  ),
]);

console.log("\n=== SUMMARY ===");
for (const [n, red] of results) {
  console.log(`${n}: ${red ? "RED ok" : "DID NOT FAIL"}`);
}
process.exit(results.some(([, r]) => !r) ? 1 : 0);
