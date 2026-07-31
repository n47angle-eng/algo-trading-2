/**
 * P2 normal insight repository wire contract.
 *
 * Normal mode treats the backend as the only active/archive truth. Every
 * response enters here as `unknown`; no TypeScript cast is allowed to turn an
 * HTTP 200 into a successful repository action.
 */

export const LIVE_INSIGHT_ORIGINS = ["workshop", "journal-app"] as const;
export const LIVE_INSIGHT_STATUSES = [
  "unverified",
  "recording",
  "supported",
  "rejected",
] as const;

export type LiveInsightOrigin = (typeof LIVE_INSIGHT_ORIGINS)[number];
export type LiveInsightStatus = (typeof LIVE_INSIGHT_STATUSES)[number];

export type InsightContractResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string };

export interface LiveInsightVersion {
  schema: "insight_version.v1";
  insight_id: string;
  origin: LiveInsightOrigin;
  version: number;
  title: string;
  instrument: string;
  asset_class: "equity_index_futures" | "commodity_futures";
  tag: string;
  validation_status: LiveInsightStatus;
  based_on_sketch: string;
  based_on_sketch_origin: LiveInsightOrigin;
  imported_from_origin: LiveInsightOrigin | null;
  content_sha256: string;
  imported_at: string;
  source_text: string;
}

export interface LiveInsightSummary {
  schema: "insight_version.v1";
  insight_id: string;
  origin: LiveInsightOrigin;
  version: number;
  title: string;
  instrument: string;
  asset_class: "equity_index_futures" | "commodity_futures";
  tag: string;
  validation_status: LiveInsightStatus;
  based_on_sketch: string;
  based_on_sketch_origin: LiveInsightOrigin;
  imported_from_origin: LiveInsightOrigin | null;
  content_sha256: string;
  imported_at: string;
  versions: number[];
  latest_version: number;
}

export interface LiveInsightList {
  schema: "insight_list.v1";
  count: number;
  insights: LiveInsightSummary[];
}

export interface LiveInsightDetail {
  schema: "insight_detail.v1";
  origin: LiveInsightOrigin;
  insight_id: string;
  latest_version: number;
  version_count: number;
  versions: LiveInsightVersion[];
}

export interface LiveInsightImport {
  schema: "insight_import.v1";
  deduplicated: boolean;
  message: string;
  insight: LiveInsightVersion;
}

export interface LiveInsightArchive {
  schema: "insight_archive.v1";
  origin: LiveInsightOrigin;
  insight_id: string;
  archive_id: string;
  deleted_at: string;
  version_count: number;
  archived_to: string;
}

export interface LiveInsightArchiveSummary {
  archive_id: string;
  origin: LiveInsightOrigin;
  insight_id: string;
  deleted_at: string;
  version_count: number;
}

export interface LiveInsightArchiveList {
  schema: "insight_archive_list.v1";
  count: number;
  archives: LiveInsightArchiveSummary[];
}

export interface LiveInsightRestore {
  schema: "insight_restore.v1";
  origin: LiveInsightOrigin;
  insight_id: string;
  archive_id: string;
  restored_at: string;
  version_count: number;
  restored_to: string;
}

export interface InsightIdentitySnapshot {
  origin: string;
  insight_id: string;
}

export interface InsightImportCandidate extends InsightIdentitySnapshot {
  version: number;
}

export interface InsightArchiveActionSnapshot extends InsightIdentitySnapshot {
  version_count: number;
}

export interface InsightRestoreActionSnapshot
  extends InsightArchiveActionSnapshot {
  archive_id: string;
}

const INSIGHT_ID = /^insight-[0-9]{3,}$/;
const SKETCH_ID = /^sketch-[0-9]{8}-[0-9]{2}$/;
const ARCHIVE_ID =
  /^delete-[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const IMPORTED_AT =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$/;
const CANONICAL_ARCHIVE_TIME =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$/;

const SUMMARY_KEYS = [
  "schema",
  "insight_id",
  "origin",
  "version",
  "title",
  "instrument",
  "asset_class",
  "tag",
  "validation_status",
  "based_on_sketch",
  "based_on_sketch_origin",
  "imported_from_origin",
  "content_sha256",
  "imported_at",
  "versions",
  "latest_version",
] as const;

const VERSION_KEYS = [
  "schema",
  "insight_id",
  "origin",
  "version",
  "title",
  "instrument",
  "asset_class",
  "tag",
  "validation_status",
  "based_on_sketch",
  "based_on_sketch_origin",
  "imported_from_origin",
  "content_sha256",
  "imported_at",
  "source_text",
] as const;

const ARCHIVE_SUMMARY_KEYS = [
  "archive_id",
  "origin",
  "insight_id",
  "deleted_at",
  "version_count",
] as const;

function fail<T>(error: string): InsightContractResult<T> {
  return { ok: false, error };
}

function ok<T>(value: T): InsightContractResult<T> {
  return { ok: true, value };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return (
    actual.length === wanted.length &&
    actual.every((key, index) => key === wanted[index])
  );
}

function isOrigin(value: unknown): value is LiveInsightOrigin {
  return value === "workshop" || value === "journal-app";
}

function isStatus(value: unknown): value is LiveInsightStatus {
  return (
    value === "unverified" ||
    value === "recording" ||
    value === "supported" ||
    value === "rejected"
  );
}

function isArchiveId(value: unknown): value is string {
  return typeof value === "string" && ARCHIVE_ID.test(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isExactNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value === value.trim();
}

function isUtcInstant(value: unknown, canonicalArchive: boolean): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const pattern = canonicalArchive ? CANONICAL_ARCHIVE_TIME : IMPORTED_AT;
  return pattern.test(value) && !Number.isNaN(Date.parse(value));
}

function parseCommonVersionFields(
  value: Record<string, unknown>,
  path: string,
): InsightContractResult<
  Omit<LiveInsightVersion, "source_text"> & { source_text?: string }
> {
  if (
    value.schema !== "insight_version.v1" ||
    typeof value.insight_id !== "string" ||
    !INSIGHT_ID.test(value.insight_id) ||
    !isOrigin(value.origin) ||
    !isPositiveInteger(value.version) ||
    !isExactNonEmptyString(value.title) ||
    !isExactNonEmptyString(value.instrument) ||
    (value.asset_class !== "equity_index_futures" &&
      value.asset_class !== "commodity_futures") ||
    !isExactNonEmptyString(value.tag) ||
    !isStatus(value.validation_status) ||
    typeof value.based_on_sketch !== "string" ||
    !SKETCH_ID.test(value.based_on_sketch) ||
    !isOrigin(value.based_on_sketch_origin) ||
    !(
      value.imported_from_origin === null ||
      isOrigin(value.imported_from_origin)
    ) ||
    typeof value.content_sha256 !== "string" ||
    !SHA256.test(value.content_sha256) ||
    !isUtcInstant(value.imported_at, false)
  ) {
    return fail(`${path} fields do not match insight_version.v1`);
  }
  return ok(
    value as unknown as Omit<LiveInsightVersion, "source_text"> & {
      source_text?: string;
    },
  );
}

function parseSummary(
  value: unknown,
  path: string,
): InsightContractResult<LiveInsightSummary> {
  if (!isObject(value) || !hasExactKeys(value, SUMMARY_KEYS)) {
    return fail(`${path} keys are not exact`);
  }
  const common = parseCommonVersionFields(value, path);
  if (!common.ok) {
    return common;
  }
  if (!Array.isArray(value.versions) || value.versions.length === 0) {
    return fail(`${path}.versions must be a non-empty array`);
  }
  let previous = 0;
  for (const version of value.versions) {
    if (!isPositiveInteger(version) || version <= previous) {
      return fail(`${path}.versions must be unique numeric ascending integers`);
    }
    previous = version;
  }
  if (
    !isPositiveInteger(value.latest_version) ||
    value.latest_version !== previous ||
    value.version !== previous
  ) {
    return fail(`${path} latest version chain does not reconcile`);
  }
  return ok(value as unknown as LiveInsightSummary);
}

function parseVersion(
  value: unknown,
  path: string,
): InsightContractResult<LiveInsightVersion> {
  if (!isObject(value) || !hasExactKeys(value, VERSION_KEYS)) {
    return fail(`${path} keys are not exact`);
  }
  const common = parseCommonVersionFields(value, path);
  if (!common.ok) {
    return common as InsightContractResult<LiveInsightVersion>;
  }
  if (typeof value.source_text !== "string" || value.source_text.length === 0) {
    return fail(`${path}.source_text must contain the exact imported document`);
  }
  return ok(value as unknown as LiveInsightVersion);
}

export function insightIdentityKey(origin: string, insightId: string): string {
  return `${origin}\u0000${insightId}`;
}

export function insightArchiveIdentityKey(
  origin: string,
  insightId: string,
  archiveId: string,
): string {
  return `${insightIdentityKey(origin, insightId)}\u0000${archiveId}`;
}

export function parseInsightActiveList(
  value: unknown,
): InsightContractResult<LiveInsightList> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ["schema", "count", "insights"]) ||
    value.schema !== "insight_list.v1" ||
    !isPositiveIntegerOrZero(value.count) ||
    !Array.isArray(value.insights) ||
    value.count !== value.insights.length
  ) {
    return fail("active insight list envelope is invalid");
  }
  const insights: LiveInsightSummary[] = [];
  const identities = new Set<string>();
  for (const [index, row] of value.insights.entries()) {
    const parsed = parseSummary(row, `insights[${index}]`);
    if (!parsed.ok) {
      return parsed as InsightContractResult<LiveInsightList>;
    }
    const key = insightIdentityKey(
      parsed.value.origin,
      parsed.value.insight_id,
    );
    if (identities.has(key)) {
      return fail(`insights[${index}] repeats a composite identity`);
    }
    identities.add(key);
    insights.push(parsed.value);
  }
  return ok({
    schema: "insight_list.v1",
    count: insights.length,
    insights,
  });
}

export function parseInsightDetail(
  value: unknown,
  expected: InsightIdentitySnapshot,
  expectedSummary?: LiveInsightSummary,
): InsightContractResult<LiveInsightDetail> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      "schema",
      "origin",
      "insight_id",
      "latest_version",
      "version_count",
      "versions",
    ]) ||
    value.schema !== "insight_detail.v1" ||
    !isOrigin(value.origin) ||
    typeof value.insight_id !== "string" ||
    !INSIGHT_ID.test(value.insight_id) ||
    value.origin !== expected.origin ||
    value.insight_id !== expected.insight_id ||
    !isPositiveInteger(value.latest_version) ||
    !isPositiveInteger(value.version_count) ||
    !Array.isArray(value.versions) ||
    value.version_count !== value.versions.length
  ) {
    return fail("insight detail envelope or route identity is invalid");
  }
  const versions: LiveInsightVersion[] = [];
  let previous = 0;
  for (const [index, row] of value.versions.entries()) {
    const parsed = parseVersion(row, `versions[${index}]`);
    if (!parsed.ok) {
      return parsed as InsightContractResult<LiveInsightDetail>;
    }
    if (
      parsed.value.origin !== value.origin ||
      parsed.value.insight_id !== value.insight_id ||
      parsed.value.version <= previous
    ) {
      return fail(`versions[${index}] identity/order does not reconcile`);
    }
    previous = parsed.value.version;
    versions.push(parsed.value);
  }
  if (previous !== value.latest_version) {
    return fail("detail latest_version does not match its final version");
  }
  if (
    expectedSummary &&
    (expectedSummary.origin !== value.origin ||
      expectedSummary.insight_id !== value.insight_id ||
      expectedSummary.latest_version !== value.latest_version ||
      expectedSummary.versions.length !== versions.length ||
      expectedSummary.versions.some(
        (version, index) => version !== versions[index]?.version,
      ))
  ) {
    return fail("detail version chain does not match the selected list row");
  }
  return ok({
    schema: "insight_detail.v1",
    origin: value.origin,
    insight_id: value.insight_id,
    latest_version: value.latest_version,
    version_count: value.version_count,
    versions,
  });
}

export function parseInsightImport(
  value: unknown,
  candidate: InsightImportCandidate,
): InsightContractResult<LiveInsightImport> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ["schema", "deduplicated", "message", "insight"]) ||
    value.schema !== "insight_import.v1" ||
    typeof value.deduplicated !== "boolean" ||
    !isExactNonEmptyString(value.message)
  ) {
    return fail("insight import envelope is invalid");
  }
  const insight = parseVersion(value.insight, "insight");
  if (!insight.ok) {
    return insight as InsightContractResult<LiveInsightImport>;
  }
  if (
    insight.value.origin !== candidate.origin ||
    insight.value.insight_id !== candidate.insight_id ||
    insight.value.version !== candidate.version
  ) {
    return fail("import response identity does not match the submitted candidate");
  }
  return ok({
    schema: "insight_import.v1",
    deduplicated: value.deduplicated,
    message: value.message,
    insight: insight.value,
  });
}

export function parseInsightArchive(
  value: unknown,
  action: InsightArchiveActionSnapshot,
): InsightContractResult<LiveInsightArchive> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      "schema",
      "origin",
      "insight_id",
      "archive_id",
      "deleted_at",
      "version_count",
      "archived_to",
    ]) ||
    value.schema !== "insight_archive.v1" ||
    !isOrigin(value.origin) ||
    typeof value.insight_id !== "string" ||
    !INSIGHT_ID.test(value.insight_id) ||
    !isArchiveId(value.archive_id) ||
    !isUtcInstant(value.deleted_at, true) ||
    !isPositiveInteger(value.version_count) ||
    typeof value.archived_to !== "string"
  ) {
    return fail("insight archive response is invalid");
  }
  const expectedPath =
    `data/insights/_deleted/${value.origin}/${value.insight_id}/` +
    value.archive_id;
  if (
    value.origin !== action.origin ||
    value.insight_id !== action.insight_id ||
    value.version_count !== action.version_count ||
    value.archived_to !== expectedPath ||
    !isSafeRepoRelativePosixPath(value.archived_to)
  ) {
    return fail("archive response identity, count, or path does not reconcile");
  }
  return ok(value as unknown as LiveInsightArchive);
}

export function parseInsightArchiveList(
  value: unknown,
): InsightContractResult<LiveInsightArchiveList> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, ["schema", "count", "archives"]) ||
    value.schema !== "insight_archive_list.v1" ||
    !isPositiveIntegerOrZero(value.count) ||
    !Array.isArray(value.archives) ||
    value.count !== value.archives.length
  ) {
    return fail("insight archive list envelope is invalid");
  }
  const archives: LiveInsightArchiveSummary[] = [];
  const archiveIds = new Set<string>();
  const compositeIdentities = new Set<string>();
  for (const [index, row] of value.archives.entries()) {
    if (
      !isObject(row) ||
      !hasExactKeys(row, ARCHIVE_SUMMARY_KEYS) ||
      !isArchiveId(row.archive_id) ||
      !isOrigin(row.origin) ||
      typeof row.insight_id !== "string" ||
      !INSIGHT_ID.test(row.insight_id) ||
      !isUtcInstant(row.deleted_at, true) ||
      !isPositiveInteger(row.version_count)
    ) {
      return fail(`archives[${index}] is invalid`);
    }
    const parsed = row as unknown as LiveInsightArchiveSummary;
    const compositeKey = insightIdentityKey(
      parsed.origin,
      parsed.insight_id,
    );
    if (archiveIds.has(parsed.archive_id)) {
      return fail(`archives[${index}] repeats a global archive id`);
    }
    if (compositeIdentities.has(compositeKey)) {
      return fail(`archives[${index}] repeats an active composite identity`);
    }
    archiveIds.add(parsed.archive_id);
    compositeIdentities.add(compositeKey);
    archives.push(parsed);
  }
  for (let index = 1; index < archives.length; index += 1) {
    if (compareArchiveRows(archives[index - 1], archives[index]) > 0) {
      return fail("archive rows are not in the frozen server order");
    }
  }
  return ok({
    schema: "insight_archive_list.v1",
    count: archives.length,
    archives,
  });
}

export function parseInsightRestore(
  value: unknown,
  action: InsightRestoreActionSnapshot,
): InsightContractResult<LiveInsightRestore> {
  if (
    !isObject(value) ||
    !hasExactKeys(value, [
      "schema",
      "origin",
      "insight_id",
      "archive_id",
      "restored_at",
      "version_count",
      "restored_to",
    ]) ||
    value.schema !== "insight_restore.v1" ||
    !isOrigin(value.origin) ||
    typeof value.insight_id !== "string" ||
    !INSIGHT_ID.test(value.insight_id) ||
    !isArchiveId(value.archive_id) ||
    !isUtcInstant(value.restored_at, true) ||
    !isPositiveInteger(value.version_count) ||
    typeof value.restored_to !== "string"
  ) {
    return fail("insight restore response is invalid");
  }
  const expectedPath = `data/insights/${value.origin}/${value.insight_id}`;
  if (
    !isArchiveId(action.archive_id) ||
    value.origin !== action.origin ||
    value.insight_id !== action.insight_id ||
    value.archive_id !== action.archive_id ||
    value.version_count !== action.version_count ||
    value.restored_to !== expectedPath ||
    !isSafeRepoRelativePosixPath(value.restored_to)
  ) {
    return fail("restore response identity, count, or path does not reconcile");
  }
  return ok(value as unknown as LiveInsightRestore);
}

function isPositiveIntegerOrZero(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isSafeRepoRelativePosixPath(value: string): boolean {
  return (
    value.length > 0 &&
    !value.startsWith("/") &&
    !value.includes("\\") &&
    !/^[A-Za-z]:/.test(value) &&
    value.split("/").every((segment) => segment !== "" && segment !== "..")
  );
}

/**
 * Array sort order helper. A positive result means `left` appears too late for
 * the required deleted_at-descending / identity-ascending order.
 */
function compareArchiveRows(
  left: LiveInsightArchiveSummary,
  right: LiveInsightArchiveSummary,
): number {
  if (left.deleted_at !== right.deleted_at) {
    return left.deleted_at > right.deleted_at ? -1 : 1;
  }
  const leftIdentity = [left.origin, left.insight_id, left.archive_id];
  const rightIdentity = [right.origin, right.insight_id, right.archive_id];
  for (let index = 0; index < leftIdentity.length; index += 1) {
    const comparison = leftIdentity[index].localeCompare(rightIdentity[index]);
    if (comparison !== 0) {
      return comparison;
    }
  }
  return 0;
}
