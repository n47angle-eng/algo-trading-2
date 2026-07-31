/**
 * Temporary mutation runner for Correction B D20 evidence.
 * Does not leave mutations in the tree.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

function runVitest(filterArgs) {
  const r = spawnSync(
    "npx",
    ["vitest", "run", ...filterArgs, "--reporter=dot"],
    { cwd: root, encoding: "utf8", shell: true },
  );
  return { code: r.status ?? 1, out: (r.stdout || "") + (r.stderr || "") };
}

function mut(name, file, transform, vitestArgs) {
  const path = resolve(root, file);
  const orig = readFileSync(path, "utf8");
  const next = transform(orig);
  if (next === orig) {
    console.log(`SKIP ${name} (no change)`);
    return false;
  }
  writeFileSync(path, next);
  const { code, out } = runVitest(vitestArgs);
  writeFileSync(path, orig);
  const red = code !== 0;
  console.log(
    `${red ? "RED " : "GREEN(!) "}${name} exit=${code} :: ${vitestArgs.join(" ")}`,
  );
  if (!red) {
    const lines = out.split("\n").filter((l) => /FAIL|Tests /.test(l));
    console.log(lines.slice(0, 5).join("\n"));
  }
  return red;
}

const results = [];

// M1 hardcode fallback when fields missing
results.push([
  "M1",
  mut(
    "M1 hardcode catalog",
    "src/lib/catalog/parseCatalog.ts",
    (s) => {
      // Replace the missing-field invalid return with hard-coded ready row
      const re =
        /if \(\s*!symbol \|\|[\s\S]*?!currency\s*\) \{\s*return \{[\s\S]*?status: "invalid",[\s\S]*?\};\s*\}/;
      const next = s.replace(
        re,
        `if (!symbol || !contractId || !displayName || !assetClassRaw || !currency) {
      return { status: "ready", rows: [{ symbol: "NQ", contractId: "H", displayName: "HARD", assetClass: "equity_index_futures", currency: "USD", sessionsAvailable: ["eth"] }] };
    }`,
      );
      return next;
    },
    [
      "src/lib/catalog/parseCatalog.test.ts",
      "-t",
      "without additive",
    ],
  ),
]);

// M2 unlock after first image
results.push([
  "M2",
  mut(
    "M2 unlock after image",
    "src/lib/sketch/store.ts",
    (s) =>
      s.replace(
        "if (draft.instrumentLocked && draft.instrument) {",
        "if (false && draft.instrumentLocked && draft.instrument) {",
      ),
    ["src/lib/sketch/instrumentLock.test.ts", "-t", "locks instrument"],
  ),
]);

// M3 rationale exact-key → skip expected keys
results.push([
  "M3",
  mut(
    "M3 rationale subset",
    "src/lib/strategy/universe.ts",
    (s) =>
      s.replace(
        "for (const k of expectedKeys) {",
        "for (const k of [] as string[]) {",
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "M3: missing expansion",
    ],
  ),
]);

// M4 remove cross-class
results.push([
  "M4",
  mut(
    "M4 remove cross-class",
    "src/lib/strategy/universe.ts",
    (s) =>
      s.replace(
        "if (row.assetClass !== universe.assetClass) {",
        "if (false && row.assetClass !== universe.assetClass) {",
      ),
    ["src/lib/strategy/universe.test.ts", "-t", "rejects NQ+GC"],
  ),
]);

// M5 legacy as missing (skip legacy gate)
results.push([
  "M5",
  mut(
    "M5 legacy as missing",
    "src/lib/strategy/universe.ts",
    (s) =>
      s.replace(
        '} else if (sketchRef.kind === "legacy_incomplete") {',
        '} else if (false && sketchRef.kind === "legacy_incomplete") {',
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "M5: legacy_incomplete",
    ],
  ),
]);

// M6 checkbox sentinel removed
results.push([
  "M6",
  mut(
    "M6 remove no-checkbox sentinel",
    "src/components/strategies/UniversePanel.tsx",
    (s) =>
      s.replace(
        'data-testid="universe-no-checkboxes"',
        'data-testid="universe-has-checkboxes"',
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "M6: universe panel",
    ],
  ),
]);

// M7 meta miss asset_class
results.push([
  "M7",
  mut(
    "M7 meta omit asset_class",
    "src/lib/sketch/metaYaml.ts",
    (s) =>
      s.replace(
        /if \(draft\.assetClass\) \{[\s\S]*?\n  \}/,
        "",
      ),
    ["src/lib/p2/instrumentP2.test.ts", "-t", "meta + strategy"],
  ),
]);

// parser skip asset_class gate
results.push([
  "M_parser",
  mut(
    "parser skip asset_class",
    "src/api/client.ts",
    (s) =>
      s.replace(
        "if (!SKETCH_ASSET_CLASSES.has(m.asset_class)) {\n      return null;\n    }",
        "if (false && !SKETCH_ASSET_CLASSES.has(m.asset_class)) {\n      return null;\n    }",
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "instrument-only / class-only",
    ],
  ),
]);

// D23: exact-value — reintroduce trim normalize (must RED)
results.push([
  "M_exact",
  mut(
    "exact trim normalize",
    "src/api/client.ts",
    (s) =>
      s
        .replace(
          "if (m.instrument !== m.instrument.trim()) {\n      return null;\n    }",
          "/* M_exact allow whitespace */",
        )
        .replace(
          "if (m.asset_class !== m.asset_class.trim()) {\n      return null;\n    }",
          "m.instrument = m.instrument.trim();\n    m.asset_class = m.asset_class.trim();",
        ),
    [
      "src/lib/p2/correctionC.test.tsx",
      "-t",
      "left/right whitespace",
    ],
  ),
]);

// Insight click skip sketch context
results.push([
  "M_insight",
  mut(
    "insight skip sketch ctx",
    "src/components/strategies/InsightTab.tsx",
    (s) =>
      s.replace(
        "const ctx = insightCtxFromSketchRef(\n                sketchRef,\n                catalog.status === \"ready\" ? catalog.rows : null,\n                catalog.status === \"ready\",\n              );",
        "const ctx = { catalogRows: catalog.status === \"ready\" ? catalog.rows : null, catalogReady: catalog.status === \"ready\" };",
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "insight import under server 5xx",
    ],
  ),
]);

// fixture revision → length collision
results.push([
  "M_revision",
  mut(
    "fixture length epoch",
    "src/pages/StrategiesPage.tsx",
    (s) =>
      s.replace(
        "const fixtureEpoch = fixtureRevision;",
        "const fixtureEpoch = activeFixture ? activeFixture.length + tab.length : 0;",
      ),
    [
      "src/lib/p2/correctionB.test.tsx",
      "-t",
      "catalog_invalid → legacy_exported",
    ],
  ),
]);

// D24: remove request-generation invalidation → late A can overwrite B
results.push([
  "M_late_a",
  mut(
    "late A ignore seq invalidate",
    "src/lib/sketch/useOwnerSketchLoad.ts",
    (s) =>
      s
        .replace(
          "if (seq !== reqSeq.current) {\n          return;\n        }\n        setState({ kind: \"ready\", detail });",
          "setState({ kind: \"ready\", detail });",
        )
        .replace(
          "if (seq !== reqSeq.current) {\n          return;\n        }\n        if (err instanceof SketchFetchError) {",
          "if (err instanceof SketchFetchError) {",
        ),
    [
      "src/lib/p2/correctionC.test.tsx",
      "-t",
      "true A pending",
    ],
  ),
]);

const failed = results.filter(([, red]) => !red);
console.log("\n=== SUMMARY ===");
for (const [n, red] of results) {
  console.log(`${n}: ${red ? "RED ok" : "DID NOT FAIL"}`);
}
process.exit(failed.length ? 1 : 0);
