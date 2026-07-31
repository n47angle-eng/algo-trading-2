/**
 * Required mutation evidence for P2 normal live preview ([183]).
 * Each edit is temporary, the target is restored byte-for-byte, and the
 * designated test is rerun GREEN before the next group.
 */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const requested = process.argv.slice(2);

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function runVitest(testName) {
  const result = spawnSync(
    process.execPath,
    [
      resolve(root, "node_modules", "vitest", "vitest.mjs"),
      "run",
      "src/lib/preview/normalLivePreview.test.tsx",
      "-t",
      testName,
      "--reporter=dot",
      "--maxWorkers=2",
    ],
    {
      cwd: root,
      encoding: "utf8",
      timeout: 60_000,
    },
  );
  return {
    code: result.status ?? 1,
    output: (result.stdout || "") + (result.stderr || ""),
  };
}

function mutate(name, file, transform, testName, requiredRedText) {
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

  let red;
  try {
    writeFileSync(path, changed, "utf8");
    red = runVitest(testName);
  } finally {
    writeFileSync(path, original);
  }
  const restored = readFileSync(path);
  if (!restored.equals(original)) {
    throw new Error(`${name}: source was not restored byte-for-byte`);
  }
  const green = runVitest(testName);
  const intendedRed =
    red.code !== 0 &&
    (!requiredRedText || red.output.includes(requiredRedText));
  const passed = intendedRed && green.code === 0;
  console.log(
    `${passed ? "RED→restore→GREEN" : "FAILED"} ${name} ` +
      `red=${red.code} green=${green.code} ` +
      `sha256=${sha256(restored)}`,
  );
  if (requiredRedText && intendedRed) {
    console.log(`  intended RED: ${requiredRedText}`);
  }
  if (!passed) {
    console.log(
      `${red.output}\n${green.output}`
        .split("\n")
        .filter((line) => /Tests|FAIL|Error/.test(line))
        .slice(0, 12)
        .join("\n"),
    );
  }
  return passed;
}

const results = [
  [
    "validate success auto-posts preview",
    mutate(
      "validate success auto-posts preview",
      "src/components/strategies/QuantifyTab.tsx",
      (source) =>
        source.replace(
          `      if (result.valid) {
        setNotice(`,
          `      if (result.valid) {
        void fetch("/api/v1/backtests/preview", {
          method: "POST",
          body: "{}",
        });
        setNotice(`,
        ),
      "normal validation never auto-posts a preview",
    ),
  ],
  [
    "persisted false and deep run_id guard removed",
    mutate(
      "persisted false and deep run_id guard removed",
      "src/lib/preview/contract.ts",
      (source) =>
        source.replace(
          "  const identityError = dryRunIdentityError(body);",
          "  const identityError = null;",
        ),
      "fails closed on persisted true",
    ),
  ],
  [
    "422 body discarded as generic failure",
    mutate(
      "422 body discarded as generic failure",
      "src/api/client.ts",
      (source) =>
        source.replace(
          "    responseBody = await response.json();",
          "    responseBody = response.ok ? await response.json() : null;",
        ),
      "renders complete 422 validation envelope",
    ),
  ],
  [
    "daily and evaluation share one scale",
    mutate(
      "daily and evaluation share one scale",
      "src/components/strategies/PreviewPanel.tsx",
      (source) =>
        source.replace(
          'data-preview-scale="daily"',
          'data-preview-scale="evaluation"',
        ),
      "keeps daily and evaluation funnels on separate scales",
    ),
  ],
  [
    "owner-review calls live preview",
    mutate(
      "owner-review calls live preview",
      "src/components/strategies/PreviewPanel.tsx",
      (source) =>
        source.replace(
          "const response: PreviewHttpResult = ownerReview\n        ? {",
          "const response: PreviewHttpResult = false\n        ? {",
        ),
      "owner-review click uses the in-memory fixture",
    ),
  ],
  [
    "late A may overwrite new B truth",
    mutate(
      "late A may overwrite new B truth",
      "src/components/strategies/PreviewPanel.tsx",
      (source) =>
        source.replace(
          `      if (
        !mountedRef.current ||
        requestGeneration !== generationRef.current
      ) {
        return;
      }`,
          `      if (!mountedRef.current) {
        return;
      }`,
        ),
      "late A response cannot overwrite new B input and result",
    ),
  ],
  [
    "validation identity acceptance guard removed",
    mutate(
      "validation identity acceptance guard removed",
      "src/components/strategies/QuantifyTab.tsx",
      (source) =>
        source.replace(
          `    const acceptsValidationResult = () =>
      mountedRef.current &&
      requestGeneration === validationGenerationRef.current &&
      validationIdentityRef.current.sourceText ===
        requestIdentity.sourceText &&
      validationIdentityRef.current.filename === requestIdentity.filename;`,
          "    const acceptsValidationResult = () => mountedRef.current;",
        ),
      "does not let stale A validation authorize source B",
      "stale A must not authorize unvalidated B preview",
    ),
  ],
];

console.log("\n=== P2 NORMAL PREVIEW MUTATION SUMMARY ===");
for (const [name, result] of results) {
  console.log(
    `${name}: ${
      result === null ? "SKIPPED" : result ? "RED→GREEN ok" : "FAILED"
    }`,
  );
}
const executed = results.filter(([, result]) => result !== null);
if (executed.length === 0 || executed.some(([, result]) => result !== true)) {
  process.exitCode = 1;
}
