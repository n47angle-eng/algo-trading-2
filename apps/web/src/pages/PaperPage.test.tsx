import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PaperPage } from "./PaperPage";
import { paperReviewMemberPaths } from "../lib/paper/reviewContract";
import { paperSha256Hex } from "../lib/paper/reviewDownload";

/*
 * P6 Stage A mounted behaviour ([205] §8.2 + approach C §9).
 *
 * Every transport here is a strict mock: no server, no browser, no real P6
 * request, no default P6 database, no IB and no Telegram.
 */

const STRATEGY_ID = "strategy-0003";
const CONTENT_SHA =
  "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97";
const CONTRACT_ID = "NQ-202609-CME";
const RUN_ID = "nq-20260728-standard-365adf";
const RESULT_SHA =
  "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7";
const TRADER_ID = "trader-1f2e3d4c5b6a798877665544332211ff";
const ACCOUNT_ID = "paper-account-aabbccddeeff00112233445566778899";
const AT = "2026-07-29T00:00:00Z";

interface Call {
  url: string;
  method: string;
  body: unknown;
  /** Router search string as it stood when the request left. */
  search: string;
}

let currentSearch = "";

/** Renders the router search so a test can assert URL-before-request order. */
function LocationProbe() {
  const location = useLocation();
  currentSearch = location.search;
  return <span data-testid="router-search">{location.search}</span>;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiError(code: string, status: number): Response {
  return json(
    {
      detail: {
        schema: "paper_api_error.v1",
        code,
        message: "人話原因",
        retryable: false,
      },
    },
    status,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((inner) => {
    resolve = inner;
  });
  return { promise, resolve };
}

/** Confirmed strategy list from `/api/v1/strategies?status=confirmed`. */
function eligibleBody(count = 1) {
  const rows = [
    {
      strategy_id: STRATEGY_ID,
      content_sha256: CONTENT_SHA,
      name: STRATEGY_ID,
      status: "confirmed",
    },
    {
      strategy_id: "strategy-0004",
      content_sha256: "b".repeat(64),
      name: "strategy-0004",
      status: "confirmed",
    },
  ].slice(0, count);
  return {
    schema: "strategy_version_list.v1",
    count: rows.length,
    versions: rows,
  };
}

function contractsBody(strategyId = STRATEGY_ID, contentSha = CONTENT_SHA) {
  return {
    schema: "paper_contract_list.v1",
    selection: { strategy_id: strategyId, content_sha256: contentSha },
    count: 1,
    contracts: [
      {
        contract_id: CONTRACT_ID,
        symbol: "NQ",
        display_name: "E-mini Nasdaq-100",
      },
    ],
  };
}

function baselinesBody(initialCapital = 100000, tradeCount = 0) {
  return {
    schema: "paper_baseline_list.v1",
    selection: {
      strategy_id: STRATEGY_ID,
      content_sha256: CONTENT_SHA,
      contract_id: CONTRACT_ID,
    },
    count: 1,
    baselines: [
      {
        run_id: RUN_ID,
        result_sha256: RESULT_SHA,
        range_start: "2026-07-22T22:00:00Z",
        range_end: "2026-07-23T21:00:00Z",
        currency: "USD",
        initial_capital: initialCapital,
        trade_count: tradeCount,
        net_r: 0,
        validation_run: false,
        integrity: "verified",
      },
    ],
  };
}

function checkRows(statuses: string[] = ["ready", "ready", "ready", "ready"]) {
  const keys = [
    "ib_realtime",
    "exchange_calendar",
    "telegram",
    "baseline_integrity",
  ];
  return keys.map((key, index) => ({
    key,
    status: statuses[index],
    reason: "isolated provider confirmed for this integration runtime",
    checked_at: AT,
  }));
}

function readinessBody(
  statuses: string[] = ["ready", "ready", "ready", "ready"],
  marketSession = "closed",
) {
  const checks = checkRows(statuses);
  return {
    schema: "paper_readiness.v1",
    selection: {
      strategy_id: STRATEGY_ID,
      content_sha256: CONTENT_SHA,
      contract_id: CONTRACT_ID,
      baseline_run_id: RUN_ID,
      baseline_result_sha256: RESULT_SHA,
    },
    overall: checks.every((row) => row.status === "ready") ? "ready" : "blocked",
    market_session: marketSession,
    checked_at: AT,
    checks,
  };
}

const OPERATION_ID = "paper-provision-0123456789abcdef0123456789abcdef";
const AUTHORIZED_AT = "2026-07-30T09:00:00Z";
const EXPIRES_AT = "2026-07-30T09:30:00Z";
const PERMIT_REASON =
  "Owner-approved one-off P6 provision-only flow verification";

/** Option A §7.2/§7.3: the exact preflight body the approved producer returns. */
function authorizationBody(
  state = "armed",
  overrides: Record<string, unknown> = {},
) {
  const authorized = state !== "disabled";
  return {
    schema: "paper_provisioning_authorization.v1",
    state,
    operation_id: authorized ? OPERATION_ID : null,
    authorized_at: authorized ? AUTHORIZED_AT : null,
    expires_at: authorized ? EXPIRES_AT : null,
    reason: PERMIT_REASON,
    ...overrides,
  };
}

function provisioningBody(
  options: {
    state?: string;
    statuses?: string[];
    marketSession?: string;
    canProvision?: boolean;
    authorization?: Record<string, unknown>;
    overrides?: Record<string, unknown>;
  } = {},
) {
  const state = options.state ?? "armed";
  const runtime = readinessBody(
    options.statuses ?? ["ready", "ready", "ready", "ready"],
    options.marketSession ?? "closed",
  );
  const baselineReady =
    runtime.checks.find((row) => row.key === "baseline_integrity")?.status ===
    "ready";
  return {
    schema: "paper_provisioning_readiness.v1",
    selection: { ...runtime.selection },
    can_provision:
      options.canProvision ?? (state === "armed" && baselineReady),
    authorization: authorizationBody(state, options.authorization ?? {}),
    runtime_readiness: runtime,
    ...(options.overrides ?? {}),
  };
}

function traderBody(requestId: string) {
  return {
    schema: "paper_trader.v1",
    trader_id: TRADER_ID,
    request_id: requestId,
    strategy: { strategy_id: STRATEGY_ID, content_sha256: CONTENT_SHA },
    contract_id: CONTRACT_ID,
    baseline: {
      run_id: RUN_ID,
      result_sha256: RESULT_SHA,
      range_start: "2026-07-22T22:00:00Z",
      range_end: "2026-07-23T21:00:00Z",
    },
    account: {
      account_id: ACCOUNT_ID,
      currency: "USD",
      initial_capital: 100000,
    },
    safeguards: { max_drawdown_r: 8, max_losing_streak: 8, blind_minutes: 5 },
    lifecycle: {
      status: "provisioned",
      reason: "simulation runtime is not activated in Stage A",
      as_of: AT,
    },
    readiness_snapshot: {
      schema: "paper_readiness_snapshot.v1",
      overall: "ready",
      market_session: "closed",
      checked_at: AT,
      checks: checkRows(),
    },
    created_at: AT,
  };
}

const LEDGER_ID = "paper-ledger-0123456789abcdef0123456789abcdef";
const SNAPSHOT_ID = "paper-review-fedcba9876543210fedcba9876543210";
const RECOVERY_REQUEST_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const READY_FILENAME = `paper-review-${TRADER_ID}-20260729T000000000000Z.zip`;
const NOT_EVALUABLE =
  "模擬引擎未啟用，所以而家未判斷得到真實偏離；呢個唔等於「冇偏離」。";

function ledgerBody(capital = 100000, traderId = TRADER_ID) {
  return {
    schema: "paper_ledger_origin.v1",
    ledger_origin_id: LEDGER_ID,
    trader_id: traderId,
    origin_at: AT,
    lifecycle: { status: "provisioned", engine_status: "not_enabled" },
    strategy: {
      strategy_id: STRATEGY_ID,
      name: "Trend engineering activation smoke",
      content_sha256: CONTENT_SHA,
    },
    contract: {
      contract_id: CONTRACT_ID,
      exchange: "CME",
      timezone: "America/Chicago",
    },
    baseline: {
      run_id: RUN_ID,
      result_sha256: RESULT_SHA,
      range_start: "2026-07-22T22:00:00Z",
      range_end: "2026-07-23T21:00:00Z",
      rejection_count: 14,
      closest_algorithm: "p5_structural_closest.v1",
      closest_rejection_refs: [
        "rejection_000014",
        "rejection_000013",
        "rejection_000012",
      ],
      members: [
        {
          path: "baseline/result.json",
          bytes: 8776,
          sha256: RESULT_SHA,
        },
        {
          path: `baseline/trades/${RUN_ID}.json`,
          bytes: 109,
          sha256:
            "9f3c4e1cf03d305abd42deadf4f0e940754d8d99cf4e9a52cf74057ed03c6b69",
        },
        {
          path: `baseline/equity/${RUN_ID}.json`,
          bytes: 81,
          sha256:
            "4b38517f2b73ed6b9a986096162d5a5cb030117db69c14e84a6309edfe898346",
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          bytes: 35136,
          sha256:
            "094ebfbc9e010311292ae4005ce58de187a0aae471c32dc18ea53868629c1d30",
        },
      ],
    },
    account: {
      account_id: ACCOUNT_ID,
      currency: "USD",
      initial_capital: capital,
      independent_account: true,
    },
    balances: {
      cash: capital,
      equity: capital,
      realized_pnl: 0,
      unrealized_pnl: 0,
    },
    high_water_marks: {
      trades: 0,
      equity: 1,
      events: 4,
      expected_decisions: 0,
      positions: 0,
      orders: 0,
    },
    positions: [],
    orders: [],
    safety: {
      state: "not_running",
      drawdown_r: 0,
      loss_streak: 0,
      max_drawdown_r: 8,
      max_losing_streak: 8,
      blind_minutes: 5,
    },
    readiness_snapshot: {
      schema: "paper_readiness_snapshot.v1",
      overall: "ready",
      market_session: "closed",
      checked_at: AT,
      checks: checkRows(),
    },
    interpretation: {
      evaluation_status: "not_evaluable",
      reason: "engine_not_enabled",
      owner_view: "模擬引擎尚未啟用；未能判斷真實模擬盤偏離。",
      categories: ["unknown"],
      supporting_evidence_refs: [
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000014",
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000013",
        },
        {
          path: `baseline/events/${RUN_ID}.json`,
          evidence_id: "rejection_000012",
        },
      ],
    },
  };
}

function reviewStatusBody(
  requestId: string,
  status: "preparing" | "ready" | "failed",
) {
  const progress =
    status === "preparing"
      ? { completed_parts: 4, total_parts: 10, current_part: "paper/events.json" }
      : status === "ready"
        ? { completed_parts: 10, total_parts: 10, current_part: null }
        : { completed_parts: 7, total_parts: 10, current_part: null };
  return {
    schema: "paper_review_status.v1",
    request_id: requestId,
    snapshot_id: SNAPSHOT_ID,
    trader_id: TRADER_ID,
    status,
    captured_at: AT,
    progress,
    ready:
      status === "ready"
        ? {
            schema: "paper_review_ready.v1",
            display_filename: `paper-review-${TRADER_ID}-20260729T000000000000Z.zip`,
            artifact_bytes: 45678,
            artifact_sha256: RESULT_SHA,
            member_count: 10,
            members: [
              "paper-review.json",
              "paper/ledger-origin.json",
              "paper/trades.json",
              "paper/equity.json",
              "paper/events.json",
              "divergence/expected-actual.json",
              "baseline/result.json",
              `baseline/trades/${RUN_ID}.json`,
              `baseline/equity/${RUN_ID}.json`,
              `baseline/events/${RUN_ID}.json`,
            ].map((path, index) => ({
              path,
              bytes: 100 + index,
              sha256: RESULT_SHA,
            })),
            terminal_opener: { bytes: 1234, sha256: RESULT_SHA },
            ready_at: AT,
          }
        : null,
    error:
      status === "failed"
        ? {
            schema: "paper_review_error.v1",
            code: "snapshot_integrity_failed",
            message: "鎖定baseline有一個member雜湊不一致。",
            retryable: false,
            request_id: requestId,
            snapshot_id: SNAPSHOT_ID,
            progress: {
              completed_parts: 7,
              total_parts: 10,
              current_part: null,
            },
            issues: [],
          }
        : null,
  };
}

function reviewError(code: string, status: number, requestId: string | null) {
  return json(
    {
      detail: {
        schema: "paper_review_error.v1",
        code,
        message: "人話原因",
        retryable: code === "snapshot_not_ready",
        request_id: requestId,
        snapshot_id: null,
        progress: null,
        issues: [],
      },
    },
    status,
  );
}

/** Trader detail GETs only: the ledger/runtime surfaces share the same prefix. */
function countDetailCalls(): number {
  return harness.calls.filter(
    (call) =>
      call.method === "GET" &&
      call.url.includes("/api/v1/paper/traders/") &&
      !call.url.includes("/ledger-origin") &&
      !call.url.includes("/review-snapshots") &&
      !call.url.includes("/runtime") &&
      !call.url.includes("/timeline") &&
      !call.url.includes("/chart") &&
      !call.url.includes("/review-v2"),
  ).length;
}

function postedReviewRequestIds(): string[] {
  return harness.calls
    .filter(
      (call) => call.method === "POST" && call.url.includes("/review-snapshots"),
    )
    .map((call) => (call.body as { request_id: string }).request_id);
}

/*
 * Phase 3 fixtures. Every ZIP is built here as raw bytes so a malformed or
 * reordered archive can be produced deliberately; nothing is downloaded, no
 * file touches disk and no clipboard outside the stub is ever used.
 */
const OPENER_TEXT =
  "你會收到一個不可變 P6 review ZIP（呢句由 backend 保存，前端唔會自己砌）。\n";

/**
 * Lets a whole verification pipeline (several real Web Crypto digests) finish.
 * A couple of microtasks are not enough, and a negative assertion that runs too
 * early would pass even when the guard it claims to prove is gone.
 */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
}

function zipMemberContent(path: string): Uint8Array {
  return new TextEncoder().encode(`{"member":"${path}"}\n`);
}

function buildStoredZip(
  entries: readonly { path: string; content: Uint8Array }[],
): Uint8Array {
  const names = entries.map((entry) => new TextEncoder().encode(entry.path));
  let localSize = 0;
  let centralSize = 0;
  for (let index = 0; index < entries.length; index += 1) {
    localSize += 30 + names[index].byteLength + entries[index].content.byteLength;
    centralSize += 46 + names[index].byteLength;
  }
  const bytes = new Uint8Array(localSize + centralSize + 22);
  const view = new DataView(bytes.buffer);
  const offsets: number[] = [];
  let cursor = 0;
  for (let index = 0; index < entries.length; index += 1) {
    const size = entries[index].content.byteLength;
    offsets.push(cursor);
    view.setUint32(cursor, 0x04034b50, true);
    view.setUint16(cursor + 4, 20, true);
    view.setUint16(cursor + 8, 0, true);
    view.setUint16(cursor + 12, 33, true);
    view.setUint32(cursor + 18, size, true);
    view.setUint32(cursor + 22, size, true);
    view.setUint16(cursor + 26, names[index].byteLength, true);
    bytes.set(names[index], cursor + 30);
    bytes.set(entries[index].content, cursor + 30 + names[index].byteLength);
    cursor += 30 + names[index].byteLength + size;
  }
  const centralOffset = cursor;
  for (let index = 0; index < entries.length; index += 1) {
    const size = entries[index].content.byteLength;
    view.setUint32(cursor, 0x02014b50, true);
    view.setUint16(cursor + 4, 20, true);
    view.setUint16(cursor + 6, 20, true);
    view.setUint16(cursor + 14, 33, true);
    view.setUint32(cursor + 20, size, true);
    view.setUint32(cursor + 24, size, true);
    view.setUint16(cursor + 28, names[index].byteLength, true);
    view.setUint32(cursor + 38, 0o600 << 16, true);
    view.setUint32(cursor + 42, offsets[index], true);
    bytes.set(names[index], cursor + 46);
    cursor += 46 + names[index].byteLength;
  }
  view.setUint32(cursor, 0x06054b50, true);
  view.setUint16(cursor + 8, entries.length, true);
  view.setUint16(cursor + 10, entries.length, true);
  view.setUint32(cursor + 12, centralSize, true);
  view.setUint32(cursor + 16, centralOffset, true);
  return bytes;
}

const MEMBER_PATHS = paperReviewMemberPaths(RUN_ID) ?? [];

async function artifactFor(
  entries: readonly { path: string; content: Uint8Array }[],
) {
  const bytes = buildStoredZip(entries);
  const members = [];
  for (const entry of entries) {
    members.push({
      path: entry.path,
      bytes: entry.content.byteLength,
      sha256: (await paperSha256Hex(entry.content)) ?? "",
    });
  }
  const openerBytes = new TextEncoder().encode(OPENER_TEXT);
  return {
    bytes,
    artifact_bytes: bytes.byteLength,
    artifact_sha256: (await paperSha256Hex(bytes)) ?? "",
    members,
    opener_bytes: openerBytes.byteLength,
    opener_sha256: (await paperSha256Hex(openerBytes)) ?? "",
  };
}

function canonicalEntries() {
  return MEMBER_PATHS.map((path) => ({
    path,
    content: zipMemberContent(path),
  }));
}

type Artifact = Awaited<ReturnType<typeof artifactFor>>;

/** One canonical artifact is enough for most tests, so it is built once. */
let canonicalArtifact: Artifact | null = null;

async function ensureArtifact(): Promise<Artifact> {
  canonicalArtifact ??= await artifactFor(canonicalEntries());
  return canonicalArtifact;
}

/** A ready status whose metadata describes the given artifact exactly. */
function readyStatusFor(requestId: string, artifact: Artifact) {
  const body = reviewStatusBody(requestId, "ready");
  const ready = body.ready;
  if (ready === null) {
    throw new Error("ready fixture missing");
  }
  return {
    ...body,
    ready: {
      ...ready,
      artifact_bytes: artifact.artifact_bytes,
      artifact_sha256: artifact.artifact_sha256,
      members: artifact.members,
      terminal_opener: {
        bytes: artifact.opener_bytes,
        sha256: artifact.opener_sha256,
      },
    },
  };
}

function zipResponse(
  bytes: Uint8Array,
  overrides: {
    status?: number;
    contentType?: string | null;
    contentDisposition?: string | null;
  } = {},
): Response {
  const headers = new Headers();
  const type =
    overrides.contentType === undefined
      ? "application/zip"
      : overrides.contentType;
  const disposition =
    overrides.contentDisposition === undefined
      ? `attachment; filename="${READY_FILENAME}"`
      : overrides.contentDisposition;
  if (type !== null) {
    headers.set("Content-Type", type);
  }
  if (disposition !== null) {
    headers.set("Content-Disposition", disposition);
  }
  return new Response(bytes, { status: overrides.status ?? 200, headers });
}

async function openerBody(
  overrides: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const encoded = new TextEncoder().encode(OPENER_TEXT);
  return {
    schema: "paper_review_terminal_opener.v1",
    snapshot_id: SNAPSHOT_ID,
    text: OPENER_TEXT,
    bytes: encoded.byteLength,
    sha256: (await paperSha256Hex(encoded)) ?? "",
    ...overrides,
  };
}

interface Harness {
  calls: Call[];
  eligible: () => Promise<Response>;
  contracts: (url: string) => Promise<Response>;
  baselines: (url: string) => Promise<Response>;
  readiness: () => Promise<Response>;
  provisioning: (body: unknown) => Promise<Response>;
  create: (body: unknown) => Promise<Response>;
  requestStatus: () => Promise<Response>;
  list: () => Promise<Response>;
  detail: (url: string) => Promise<Response>;
  ledger: (url: string) => Promise<Response>;
  reviewCreate: (body: unknown) => Promise<Response>;
  reviewStatus: (url: string) => Promise<Response>;
  reviewDownload: (url: string) => Promise<Response>;
  reviewOpener: (url: string) => Promise<Response>;
}

/**
 * `userEvent.setup()` installs its own clipboard stub, so the probe has to be
 * reinstalled after it or the page would write into user-event's copy.
 */
function installClipboardProbe(
  behaviour: "resolve" | "reject" = "resolve",
): ReturnType<typeof vi.fn> {
  const writeText = vi.fn((text: string) => {
    browser.copied.push(text);
    return behaviour === "resolve"
      ? Promise.resolve()
      : Promise.reject(new Error("denied"));
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    writable: true,
    value: { writeText },
  });
  return writeText;
}

interface BrowserProbe {
  objectUrls: { url: string; type: string; size: number }[];
  revoked: string[];
  anchorClicks: { href: string; download: string; connected: boolean }[];
  copied: string[];
  storageWrites: number;
}

let harness: Harness;
let browser: BrowserProbe;

function countCalls(fragment: string, method?: string): number {
  return harness.calls.filter(
    (call) =>
      call.url.includes(fragment) &&
      (method === undefined || call.method === method),
  ).length;
}

function createdRequestIds(): string[] {
  return harness.calls
    .filter(
      (call) =>
        call.method === "POST" && call.url.endsWith("/api/v1/paper/traders"),
    )
    .map((call) => (call.body as { request_id: string }).request_id);
}

beforeEach(() => {
  harness = {
    calls: [],
    eligible: () => Promise.resolve(json(eligibleBody())),
    contracts: () => Promise.resolve(json(contractsBody())),
    baselines: () => Promise.resolve(json(baselinesBody())),
    readiness: () => Promise.resolve(json(readinessBody())),
    provisioning: () => Promise.resolve(json(provisioningBody())),
    create: (body) =>
      Promise.resolve(
        json(traderBody((body as { request_id: string }).request_id), 201),
      ),
    requestStatus: () =>
      Promise.resolve(
        json({
          schema: "paper_trader_request_status.v1",
          request_id: createdRequestIds()[0],
          status: "completed",
          trader: traderBody(createdRequestIds()[0]),
        }),
      ),
    list: () =>
      Promise.resolve(json({ schema: "paper_trader_list.v1", count: 0, traders: [] })),
    detail: () =>
      Promise.resolve(json(traderBody(createdRequestIds()[0] ?? ""))),
    ledger: () => Promise.resolve(json(ledgerBody())),
    reviewCreate: (body) =>
      Promise.resolve(
        json(
          reviewStatusBody(
            (body as { request_id: string }).request_id,
            "preparing",
          ),
          202,
        ),
      ),
    reviewStatus: (url) => {
      const requestId = url.split("/").pop() ?? "";
      return Promise.resolve(json(reviewStatusBody(requestId, "preparing")));
    },
    reviewDownload: async () => zipResponse((await ensureArtifact()).bytes),
    reviewOpener: async () => json(await openerBody()),
  };

  browser = {
    objectUrls: [],
    revoked: [],
    anchorClicks: [],
    copied: [],
    storageWrites: 0,
  };
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn((blob: Blob) => {
      const url = `blob:paper/${String(browser.objectUrls.length)}`;
      browser.objectUrls.push({ url, type: blob.type, size: blob.size });
      return url;
    }),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn((url: string) => {
      browser.revoked.push(url);
    }),
  });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    writable: true,
    value: {
      writeText: vi.fn((text: string) => {
        browser.copied.push(text);
        return Promise.resolve();
      }),
    },
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function clickSpy(this: HTMLAnchorElement) {
      browser.anchorClicks.push({
        href: this.getAttribute("href") ?? "",
        download: this.getAttribute("download") ?? "",
        connected: this.isConnected,
      });
    },
  );
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    browser.storageWrites += 1;
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body =
        typeof init?.body === "string"
          ? (JSON.parse(init.body) as unknown)
          : null;
      harness.calls.push({ url, method, body, search: currentSearch });

      if (url.includes("/api/v1/strategies?status=confirmed")) {
        return harness.eligible();
      }
      if (url.includes("/api/v1/paper/contracts")) {
        return harness.contracts(url);
      }
      if (url.includes("/api/v1/paper/baselines")) {
        return harness.baselines(url);
      }
      if (url.endsWith("/api/v1/paper/provisioning-readiness")) {
        return harness.provisioning(body);
      }
      if (url.endsWith("/api/v1/paper/readiness")) {
        return harness.readiness();
      }
      if (url.includes("/api/v1/paper/trader-requests/")) {
        return harness.requestStatus();
      }
      if (url.includes("/ledger-origin")) {
        return harness.ledger(url);
      }
      if (url.includes("/download")) {
        return harness.reviewDownload(url);
      }
      if (url.includes("/terminal-opener")) {
        return harness.reviewOpener(url);
      }
      if (url.includes("/review-snapshots")) {
        return harness.reviewCreate(body);
      }
      if (url.includes("/api/v1/paper/review-requests/")) {
        return harness.reviewStatus(url);
      }
      if (url.endsWith("/api/v1/paper/traders")) {
        return method === "POST" ? harness.create(body) : harness.list();
      }
      if (url.includes("/fleet-overview")) {
        return json({
          schema: "paper_fleet_overview.v1",
          gateway: {
            status: "unavailable",
            message: "fixture Gateway not connected",
            port_reachable: false,
            host: "127.0.0.1",
            port: 7498,
            last_bar_at: null,
          },
          totals: {
            trader_count: 0,
            running_count: 0,
            open_position_count: 0,
            total_realized_pnl: 0,
            total_unrealized_pnl: 0,
            total_equity: 0,
          },
          traders: [],
          as_of: "2026-07-31T00:00:00Z",
          closed_loop: {
            from_results: "/results",
            from_strategies: "/strategies",
            data: "/data",
            backtest: "/backtest",
          },
        });
      }
      if (url.includes("/runtime/gateway-status")) {
        return json({
          schema: "paper_gateway_status.v1",
          status: "unavailable",
          message: "fixture",
          port_reachable: false,
          host: "127.0.0.1",
          port: 7498,
          as_of: "2026-07-31T00:00:00Z",
        });
      }
      // Runtime panel polls (honest 404 when Stage A-only fixture has no v4 row).
      if (
        url.includes("/runtime") ||
        url.includes("/timeline") ||
        url.includes("/chart") ||
        url.includes("/review-v2") ||
        url.includes("/runtime/replay")
      ) {
        return json(
          {
            schema: "paper_runtime_error.v1",
            code: "trader_not_found",
            message: "fixture has no runtime row",
            retryable: false,
          },
          404,
        );
      }
      if (url.includes("/api/v1/paper/traders/")) {
        return harness.detail(url);
      }
      throw new Error(`unrouted request: ${method} ${url}`);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(URL, "createObjectURL");
  Reflect.deleteProperty(URL, "revokeObjectURL");
  Reflect.deleteProperty(navigator, "clipboard");
});

function renderPage(entry = "/paper") {
  currentSearch = "";
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <PaperPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

async function openNewTraderTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "＋ 新增交易員" }));
}

async function chooseStrategy(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /strategy-0003/ }));
}

async function chooseContract(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: /E-mini Nasdaq-100/ }),
  );
}

async function chooseBaseline(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /筆成交/ }));
}

async function reachReadyState(user: ReturnType<typeof userEvent.setup>) {
  await openNewTraderTab(user);
  await chooseStrategy(user);
  await chooseContract(user);
  await chooseBaseline(user);
  await screen.findByText("已經攞到實時價格。");
}

describe("P6 Stage A — page identity and honest defaults", () => {
  it("is a real page, not the generic route stub, and states the engine is off", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "模擬盤" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("route: /paper")).not.toBeInTheDocument();
    expect(screen.queryByText("Placeholder")).not.toBeInTheDocument();
    expect(
      screen.getAllByText(/模擬引擎尚未啟用/).length,
    ).toBeGreaterThan(0);
  });

  it("shows an honest empty overview with no fabricated trader", async () => {
    renderPage();
    expect(await screen.findByText("你而家冇任何交易員。")).toBeInTheDocument();
    const overview = screen.getByRole("region", { name: "模擬盤總覽" });
    expect(within(overview).queryByText(/R$/)).not.toBeInTheDocument();
    expect(within(overview).queryByText(/USD/)).not.toBeInTheDocument();
    expect(within(overview).queryByText(/持倉/)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("tab")).toHaveLength(2);
  });

  it("guides to the strategies page when confirmed store is empty", async () => {
    harness.eligible = () =>
      Promise.resolve(
        json({ schema: "strategy_version_list.v1", count: 0, versions: [] }),
      );
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    expect(
      await screen.findByText(
        "而家資料庫入面未有已確認策略，所以暫時冇得揀。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "去策略工作台" }),
    ).toHaveAttribute("href", "/strategies");
    expect(countCalls("/api/v1/paper/contracts")).toBe(0);
  });
});

describe("P6 Stage A — contract candidates (approach C)", () => {
  it("only asks the dedicated contract endpoint, never coverage or the run list", async () => {
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await screen.findByRole("button", { name: /E-mini Nasdaq-100/ });

    expect(countCalls("/api/v1/paper/contracts")).toBe(1);
    expect(countCalls("/api/v1/data/coverage")).toBe(0);
    expect(countCalls("/api/v1/runs")).toBe(0);
    const contractCall = harness.calls.find((call) =>
      call.url.includes("/api/v1/paper/contracts"),
    );
    expect(contractCall?.url).toContain(`strategy_id=${STRATEGY_ID}`);
    expect(contractCall?.url).toContain(`content_sha256=${CONTENT_SHA}`);
  });

  it("does not auto-select even when exactly one contract is returned", async () => {
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await screen.findByRole("button", { name: /E-mini Nasdaq-100/ });

    expect(
      screen.getByRole("button", { name: /E-mini Nasdaq-100/ }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(countCalls("/api/v1/paper/baselines")).toBe(0);
    expect(screen.getByText(/仲欠：合約/)).toBeInTheDocument();
  });

  it("fails closed on an empty contract list and keeps create disabled", async () => {
    harness.contracts = () =>
      Promise.resolve(
        json({
          schema: "paper_contract_list.v1",
          selection: { strategy_id: STRATEGY_ID, content_sha256: CONTENT_SHA },
          count: 0,
          contracts: [],
        }),
      );
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);

    expect(
      await screen.findByText(/暫時冇呢個策略嘅可用對照基準/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "建立交易員" }),
    ).toBeDisabled();
  });

  it("fails closed on a known 409 and on an unknown contract payload", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    harness.contracts = () =>
      Promise.resolve(apiError("eligible_strategy_required", 409));
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    expect(
      await screen.findByText(/後端暫時冇可用策略或對照基準/),
    ).toBeInTheDocument();

    harness.contracts = () => Promise.resolve(json({ unexpected: true }));
    await user.click(screen.getByRole("button", { name: /strategy-0004/ }));
    expect(await screen.findByText("合約清單讀唔到。")).toBeInTheDocument();
  });

  it("drops a late contract response for a superseded strategy", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    const slow = deferred<Response>();
    let call = 0;
    harness.contracts = (url) => {
      call += 1;
      if (call === 1) {
        return slow.promise;
      }
      return Promise.resolve(
        json(
          url.includes("strategy-0004")
            ? {
                schema: "paper_contract_list.v1",
                selection: {
                  strategy_id: "strategy-0004",
                  content_sha256: "b".repeat(64),
                },
                count: 1,
                contracts: [
                  {
                    contract_id: "YM-202609-CBOT",
                    symbol: "YM",
                    display_name: "E-mini Dow",
                  },
                ],
              }
            : contractsBody(),
        ),
      );
    };

    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await user.click(screen.getByRole("button", { name: /strategy-0004/ }));
    await screen.findByRole("button", { name: /E-mini Dow/ });

    slow.resolve(json(contractsBody()));
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /E-mini Dow/ }),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /E-mini Nasdaq-100/ }),
    ).not.toBeInTheDocument();
  });
});

describe("P6 Stage A — baseline selection", () => {
  it("never auto-selects a baseline and treats zero trades as legal", async () => {
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);

    const row = await screen.findByRole("button", { name: /筆成交/ });
    expect(row).toHaveAttribute("aria-pressed", "false");
    expect(within(row).getByText(/0 筆成交/)).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/provisioning-readiness")).toBe(0);
    expect(screen.getByText(/仲欠：對照基準/)).toBeInTheDocument();
    expect(screen.getByText("未揀對照基準，所以帳戶金額仲未決定。")).toBeInTheDocument();
  });

  it("shows the account amount from the response, never a hard-coded 100,000", async () => {
    harness.baselines = () => Promise.resolve(json(baselinesBody(250000.5, 3)));
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await user.click(await screen.findByRole("button", { name: /筆成交/ }));

    expect(await screen.findByText("USD 250,000.5")).toBeInTheDocument();
    expect(screen.queryByText("USD 100,000")).not.toBeInTheDocument();
  });

  it("clears baseline, readiness and confirmation when the contract changes", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    expect(screen.getByText("已經攞到實時價格。")).toBeInTheDocument();

    // Re-selecting the contract is a contract transition: everything after it
    // must be cleared, and a fresh baseline request must be issued.
    await user.click(screen.getByRole("button", { name: /E-mini Nasdaq-100/ }));
    await waitFor(() => {
      expect(screen.queryByText("已經攞到實時價格。")).not.toBeInTheDocument();
    });
    expect(screen.getByText("未揀對照基準，所以帳戶金額仲未決定。")).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/baselines")).toBe(2);
  });

  it("clears the whole downstream chain when the strategy changes", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);

    await user.click(screen.getByRole("button", { name: /strategy-0004/ }));
    await waitFor(() => {
      expect(screen.getByText("揀咗合約先會列出可揀嘅對照基準。")).toBeInTheDocument();
    });
    expect(screen.queryByText("已經攞到實時價格。")).not.toBeInTheDocument();
    expect(screen.getByText(/仲欠：合約/)).toBeInTheDocument();
  });
});

describe("P6 Stage A — readiness", () => {
  it("shows exactly four checks in the approved order", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);

    const names = screen
      .getAllByText(
        /^(IB 實時價格|交易所行事曆|Telegram 通知|對照基準完整性)$/,
      )
      .map((node) => node.textContent);
    expect(names).toEqual([
      "IB 實時價格",
      "交易所行事曆",
      "Telegram 通知",
      "對照基準完整性",
    ]);
  });

  it("treats a closed market as informative, not blocking", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);

    expect(
      screen.getByText(/而家係休市。唔係錯誤/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
  });

  /*
   * Option A §5.2: an external check that is unknown or blocked is still shown
   * exactly as it is, but it no longer blocks an authorized provision-only
   * create on its own. Authorization is the gate; runtime truth is separate.
   */
  it("keeps an unknown external check honest without blocking an authorized create", async () => {
    harness.provisioning = () =>
      Promise.resolve(
        json(provisioningBody({ statuses: ["ready", "ready", "unknown", "ready"] })),
      );
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);

    expect(
      await screen.findByText(/確認唔到通知係咪通得到/),
    ).toBeInTheDocument();
    expect(screen.getByText("未能確認")).toBeInTheDocument();
    expect(
      screen.getByText(/實際啟動條件仲未齊，所以建立之後模擬引擎唔會開始/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("已授權：可以建立一個未啟用嘅交易員。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
    expect(screen.queryByText(/仲欠：/)).not.toBeInTheDocument();
  });

  it("keeps a blocked external check honest and keeps the selection", async () => {
    harness.provisioning = () =>
      Promise.resolve(
        json(provisioningBody({ statuses: ["blocked", "ready", "ready", "ready"] })),
      );
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);

    expect(
      await screen.findByText(/而家攞唔到實時價格/),
    ).toBeInTheDocument();
    expect(screen.getByText("未通過")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
    expect(screen.getByText("USD 100,000")).toBeInTheDocument();
  });

  it("drops a late readiness response after the selection changed", async () => {
    const slow = deferred<Response>();
    let call = 0;
    harness.provisioning = () => {
      call += 1;
      return call === 1
        ? slow.promise
        : Promise.resolve(
            json(
              provisioningBody({
                statuses: ["blocked", "ready", "ready", "ready"],
                state: "disabled",
              }),
            ),
          );
    };
    const user = userEvent.setup();
    renderPage();
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);

    // Contract transition supersedes the in-flight readiness request.
    await user.click(screen.getByRole("button", { name: /E-mini Nasdaq-100/ }));
    await chooseBaseline(user);
    await screen.findByText("而家攞唔到實時價格，所以建立之後引擎唔會開始。");

    slow.resolve(json(provisioningBody()));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();
    });
    expect(screen.queryByText("已經攞到實時價格。")).not.toBeInTheDocument();
  });

  it("issues one request per recheck click", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    expect(countCalls("/api/v1/paper/provisioning-readiness")).toBe(1);

    await user.click(screen.getByRole("button", { name: "重新檢查" }));
    await waitFor(() => {
      expect(countCalls("/api/v1/paper/provisioning-readiness")).toBe(2);
    });
  });
});

describe("P6 Stage A — confirmation and creation", () => {
  it("confirms the exact locked selection before creating", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));

    const dialog = await screen.findByRole("dialog", {
      name: "確認建立交易員",
    });
    expect(within(dialog).getByText(/strategy-0003/)).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(CONTRACT_ID))).toBeInTheDocument();
    expect(within(dialog).getByText(new RegExp(RUN_ID))).toBeInTheDocument();
    expect(within(dialog).getByText("USD 100,000")).toBeInTheDocument();
    expect(
      within(dialog).getByText("最大回撤 8R · 連續蝕 8 單 · 失明 5 分鐘"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/而家係休市/)).toBeInTheDocument();
    expect(
      within(dialog).getByText(/只攞 Interactive Brokers 嘅實時價格/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/IB paper/i)).not.toBeInTheDocument();
  });

  it("sends exactly one POST for a double click and submits only the approved keys", async () => {
    const gate = deferred<Response>();
    harness.create = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));

    const confirm = await screen.findByRole("button", {
      name: "確認並建立交易員",
    });
    // Three clicks inside one act: the disabled attribute has not been applied
    // yet, so only the in-flight guard can stop the second and third POST.
    await act(async () => {
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // And the rendered button is disabled for any further click.
    await user.click(confirm);

    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    const body = harness.calls.find(
      (call) => call.method === "POST" && call.url.endsWith("/paper/traders"),
    )?.body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      "request_id",
      "schema",
      "selection",
    ]);
    expect(String(body.request_id)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );

    gate.resolve(json(traderBody(String(body.request_id)), 201));
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
  });

  it("accepts a 201 first creation and shows the provisioned truth", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/已建立 · 新分頁已經加咗喺上面/),
    ).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
  });

  it("accepts a 200 replay of the same request", async () => {
    harness.create = (body) =>
      Promise.resolve(
        json(traderBody((body as { request_id: string }).request_id), 200),
      );
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/已建立 · 新分頁已經加咗喺上面/),
    ).toBeInTheDocument();
  });

  it("fails closed on a request-id conflict without creating anything", async () => {
    harness.create = () => Promise.resolve(apiError("request_id_conflict", 409));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/系統唔會建立第二個交易員/),
    ).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(screen.queryByText(/已建立 · 新分頁/)).not.toBeInTheDocument();
  });

  it("reports 503 activation as not enabled and keeps the selection", async () => {
    harness.create = () =>
      Promise.resolve(apiError("activation_not_authorized", 503));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/模擬盤建立功能尚未啟用/),
    ).toBeInTheDocument();
    // Not an empty success: no trader tab and the selection survives.
    expect(screen.queryByText(/已建立 · 新分頁/)).not.toBeInTheDocument();
    expect(screen.getByText("USD 100,000")).toBeInTheDocument();
  });
});

describe("P6 Stage A — unknown outcome recovery", () => {
  it("looks the same request up after an unknown POST and adopts the found trader", async () => {
    harness.create = () => Promise.reject(new TypeError("network down"));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/已建立 · 新分頁已經加咗喺上面/),
    ).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(countCalls("/api/v1/paper/trader-requests/")).toBe(1);
  });

  it("only resends the same request identity after a positive 404", async () => {
    harness.create = () => Promise.reject(new TypeError("network down"));
    harness.requestStatus = () =>
      Promise.resolve(apiError("request_not_found", 404));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    const resend = await screen.findByRole("button", {
      name: "用同一個請求再送一次",
    });
    const firstId = createdRequestIds()[0];
    harness.create = (body) =>
      Promise.resolve(
        json(traderBody((body as { request_id: string }).request_id), 201),
      );
    await user.click(resend);

    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
    const ids = createdRequestIds();
    expect(ids).toHaveLength(2);
    expect(ids[1]).toBe(firstId);
  });

  it("stays unknown and never mints a second request id when the lookup is unknown", async () => {
    harness.create = () => Promise.reject(new TypeError("network down"));
    harness.requestStatus = () =>
      Promise.resolve(apiError("store_unavailable", 503));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/建立結果未知。系統唔會自己再建立多一個/),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "再查一次今次請求" }),
    );
    await waitFor(() => {
      expect(countCalls("/api/v1/paper/trader-requests/")).toBe(2);
    });
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(new Set(createdRequestIds()).size).toBe(1);
    expect(screen.queryByText(/已建立 · 新分頁/)).not.toBeInTheDocument();
  });
});

describe("P6 Stage A — provisioned trader truth", () => {
  it("shows the same identity in the tab, the list and the detail with zero fake rows", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(createdRequestIds()[0])],
        }),
      );
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    const detailPanel = await screen.findByRole("region", {
      name: "交易員詳情",
    });
    expect(
      within(detailPanel).getByText("已建立；模擬引擎尚未啟用"),
    ).toBeInTheDocument();
    expect(
      within(detailPanel).getAllByText("模擬引擎尚未啟用。").length,
    ).toBeGreaterThan(0);
    expect(
      within(detailPanel).getAllByText(new RegExp(RUN_ID)).length,
    ).toBeGreaterThan(0);
    expect(
      within(detailPanel).getAllByText(/USD 100,000/).length,
    ).toBeGreaterThan(0);
    expect(within(detailPanel).queryByText(/運行中/)).not.toBeInTheDocument();
    expect(within(detailPanel).queryByText(/已連接/)).not.toBeInTheDocument();
    expect(within(detailPanel).queryByText(/IB paper/i)).not.toBeInTheDocument();

    expect(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID}` }),
    ).toBeInTheDocument();
    // Detail + ledger must load for the new tab; StrictMode may double-invoke.
    expect(countDetailCalls()).toBeGreaterThanOrEqual(1);
    expect(countCalls("/ledger-origin")).toBeGreaterThanOrEqual(1);
  });

  it("fails closed when the list record and the detail record disagree", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(createdRequestIds()[0])],
        }),
      );
    harness.detail = () => {
      const drifted = traderBody(createdRequestIds()[0]);
      drifted.account.initial_capital = 999999;
      return Promise.resolve(json(drifted));
    };
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    expect(
      await screen.findByText(/清單同詳情記錄唔一致/),
    ).toBeInTheDocument();
    expect(screen.queryByText("USD 999,999")).not.toBeInTheDocument();
  });
});

describe("P6 Stage A — blast radius", () => {
  it("touches only the eight approved P6 endpoints", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);

    const allowed = [
      // Page independence: confirmed strategies from strategy store (not promotion).
      "/api/v1/strategies?status=confirmed",
      "/api/v1/paper/contracts",
      "/api/v1/paper/baselines",
      "/api/v1/paper/provisioning-readiness",
      "/api/v1/paper/trader-requests/",
      "/api/v1/paper/traders",
      // P6 runtime fleet / gateway / per-trader surfaces (post Stage A)
      "/api/v1/paper/fleet-overview",
      "/api/v1/paper/runtime/gateway-status",
      "/api/v1/paper/runtime-capabilities",
      "/api/v1/paper/traders/",
      "/runtime",
      "/timeline",
      "/chart",
      "/ledger-origin",
    ];
    for (const call of harness.calls) {
      expect(allowed.some((prefix) => call.url.includes(prefix))).toBe(true);
    }
    // P2 sketches / P3 data / P4 batch / promotion gate, IB, export and paper-review
    // must stay untouched by the create path. Confirmed strategies are allowed.
    for (const forbidden of [
      "/api/v1/sketches",
      "/api/v1/insights",
      "/api/v1/data/coverage",
      "/api/v1/runs",
      "/api/v1/batches",
      "/api/v1/promotion-decisions",
      "/export",
      "/paper-review",
      "ib-status",
    ]) {
      expect(countCalls(forbidden)).toBe(0);
    }
  });

  it("writes nothing to localStorage", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    expect(window.localStorage.length).toBe(0);
  });

  it("highlights results-page context without selecting anything", async () => {
    const user = userEvent.setup();
    renderPage(`/paper?strategy=${STRATEGY_ID}&run=${RUN_ID}`);
    await openNewTraderTab(user);

    const row = await screen.findByRole("button", { name: /strategy-0003/ });
    expect(within(row).getByText("你啱啱睇嗰個")).toBeInTheDocument();
    expect(row).toHaveAttribute("aria-pressed", "false");
    expect(countCalls("/api/v1/paper/contracts")).toBe(0);
    expect(countCalls("/api/v1/paper/baselines")).toBe(0);
  });
});

// --- Correction A: intent lock and latest-response authority ----------------

const TRADER_ID_B = "trader-99887766554433221100ffeeddccbbaa";
const REQUEST_A = "11111111-1111-4111-8111-111111111111";
const REQUEST_B = "22222222-2222-4222-8222-222222222222";
const CONTRACT_ID_B = "YM-202609-CBOT";

function traderBodyB(requestId: string) {
  const record = traderBody(requestId);
  record.trader_id = TRADER_ID_B;
  record.contract_id = CONTRACT_ID_B;
  return record;
}

describe("Correction A — create intent lock", () => {
  it("keeps the selection locked while a create POST is pending", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    const gate = deferred<Response>();
    harness.create = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    const other = screen.getByRole("button", { name: /strategy-0004/ });
    expect(other).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /E-mini Nasdaq-100/ }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: /筆成交/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新檢查" })).toBeDisabled();

    // The handler itself must refuse, not only the disabled attribute.
    await act(async () => {
      other.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(
      screen.getByRole("button", { name: /strategy-0003/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(countCalls("/api/v1/paper/contracts")).toBe(1);
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(new Set(createdRequestIds()).size).toBe(1);

    gate.resolve(json(traderBody(createdRequestIds()[0]), 201));
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
  });

  it("drops an old create response that does not match the locked intent", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    const gate = deferred<Response>();
    harness.create = () => gate.promise;
    // Keep the recovery lookup unresolved so the create outcome stays visible.
    harness.requestStatus = () =>
      Promise.resolve(apiError("store_unavailable", 503));
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    // A superseded selection cannot even be chosen while the intent is locked.
    await act(async () => {
      screen
        .getByRole("button", { name: /strategy-0004/ })
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // The late response describes a different selection: it must never be
    // adopted as this intent's trader.
    const stale = traderBody(createdRequestIds()[0]);
    stale.contract_id = CONTRACT_ID_B;
    gate.resolve(json(stale, 201));

    expect(
      await screen.findByText(/建立結果未知。系統唔會自己再建立多一個/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/已建立 · 新分頁/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: new RegExp(CONTRACT_ID_B) }),
    ).not.toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
  });

  it("keeps the selection locked while an unknown outcome is being looked up", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    harness.create = () => Promise.reject(new TypeError("network down"));
    const gate = deferred<Response>();
    harness.requestStatus = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    await waitFor(() => {
      expect(countCalls("/api/v1/paper/trader-requests/")).toBe(1);
    });
    const other = screen.getByRole("button", { name: /strategy-0004/ });
    expect(other).toBeDisabled();
    await act(async () => {
      other.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(
      screen.getByRole("button", { name: /strategy-0003/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);

    gate.resolve(apiError("request_not_found", 404));
    // A positive 404 is the only unlock: the same request identity is reused.
    const resend = await screen.findByRole("button", {
      name: "用同一個請求再送一次",
    });
    expect(screen.getByRole("button", { name: /strategy-0004/ })).toBeEnabled();

    harness.create = (body) =>
      Promise.resolve(
        json(traderBody((body as { request_id: string }).request_id), 201),
      );
    await user.click(resend);
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
    const ids = createdRequestIds();
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(1);
  });
});

describe("Correction A — latest-response authority", () => {
  it("keeps the newest tab detail when an older tab response lands last", async () => {
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 2,
          traders: [traderBody(REQUEST_A), traderBodyB(REQUEST_B)],
        }),
      );
    const slowA = deferred<Response>();
    harness.detail = (url) =>
      url.includes(TRADER_ID_B)
        ? Promise.resolve(json(traderBodyB(REQUEST_B)))
        : slowA.promise;

    const user = userEvent.setup();
    renderPage();
    const tabA = await screen.findByRole("tab", {
      name: `${STRATEGY_ID} · ${CONTRACT_ID}`,
    });
    await user.click(tabA);
    await user.click(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID_B}` }),
    );
    const panel = await screen.findByRole("region", { name: "交易員詳情" });
    expect(
      within(panel).getAllByText(new RegExp(CONTRACT_ID_B)).length,
    ).toBeGreaterThan(0);

    await act(async () => {
      slowA.resolve(json(traderBody(REQUEST_A)));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      within(panel).getAllByText(new RegExp(CONTRACT_ID_B)).length,
    ).toBeGreaterThan(0);
    expect(within(panel).queryAllByText(new RegExp(CONTRACT_ID))).toHaveLength(0);
    expect(within(panel).queryByText(/清單同詳情記錄唔一致/)).not.toBeInTheDocument();
    expect(within(panel).queryByText("呢個交易員讀唔到。")).not.toBeInTheDocument();
  });

  it("keeps the newest trader list when the older mount list lands last", async () => {
    const slowMount = deferred<Response>();
    let listCall = 0;
    harness.list = () => {
      listCall += 1;
      if (listCall === 1) {
        return slowMount.promise;
      }
      return Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(createdRequestIds()[0])],
        }),
      );
    };
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);

    // The stale empty mount list must not wipe the freshly created trader.
    // Flush inside act so the late response is fully processed before asserting.
    await act(async () => {
      slowMount.resolve(
        json({ schema: "paper_trader_list.v1", count: 0, traders: [] }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID}` }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "交易員詳情" })).toBeInTheDocument();
    const panel = screen.getByRole("region", { name: "交易員詳情" });
    expect(within(panel).getByText("已建立；模擬引擎尚未啟用")).toBeInTheDocument();
    expect(within(panel).queryByText(/清單同詳情記錄唔一致/)).not.toBeInTheDocument();
  });
});

// --- Correction B: same-batch create authority ------------------------------

describe("Correction B — same-batch create authority", () => {
  it("refuses a strategy change dispatched in the same batch as confirm", async () => {
    harness.eligible = () => Promise.resolve(json(eligibleBody(2)));
    const gate = deferred<Response>();
    harness.create = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));

    const confirm = await screen.findByRole("button", {
      name: "確認並建立交易員",
    });
    const other = screen.getByRole("button", { name: /strategy-0004/ });
    const contractsBefore = countCalls("/api/v1/paper/contracts");
    const baselinesBefore = countCalls("/api/v1/paper/baselines");

    // One batch: the second handler must not see a stale createLocked=false.
    await act(async () => {
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      other.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(
      screen.getByRole("button", { name: /strategy-0003/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /strategy-0004/ }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(countCalls("/api/v1/paper/contracts")).toBe(contractsBefore);
    expect(countCalls("/api/v1/paper/baselines")).toBe(baselinesBefore);
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(new Set(createdRequestIds()).size).toBe(1);

    // The original intent still adopts its own trader.
    const requestId = createdRequestIds()[0];
    await act(async () => {
      gate.resolve(json(traderBody(requestId), 201));
      await Promise.resolve();
      await Promise.resolve();
    });
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
  });

  it("refuses a recheck dispatched in the same batch as confirm", async () => {
    const gate = deferred<Response>();
    harness.create = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    await user.click(screen.getByRole("button", { name: "建立交易員" }));

    const confirm = await screen.findByRole("button", {
      name: "確認並建立交易員",
    });
    const recheck = screen.getByRole("button", { name: "重新檢查" });
    const readinessBefore = countCalls("/api/v1/paper/provisioning-readiness");

    await act(async () => {
      confirm.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      recheck.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(countCalls("/api/v1/paper/provisioning-readiness")).toBe(readinessBefore);
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(new Set(createdRequestIds()).size).toBe(1);

    const requestId = createdRequestIds()[0];
    await act(async () => {
      gate.resolve(json(traderBody(requestId), 201));
      await Promise.resolve();
      await Promise.resolve();
    });
    await screen.findByText(/已建立 · 新分頁已經加咗喺上面/);
    expect(countCalls("/api/v1/paper/provisioning-readiness")).toBe(readinessBefore);
  });
});

// --- Stage B Phase 2: ledger card and request recovery ----------------------

const REVIEW_CTA = "建立 review snapshot";

/** Create one trader through the Stage A flow, then open its tab. */
/**
 * The review CTA stays disabled until the ledger answer lands. Clicking it too
 * early is a silent no-op, so every review flow waits for it to be enabled.
 */
async function clickReviewCta(
  user: ReturnType<typeof userEvent.setup>,
): Promise<void> {
  const cta = await screen.findByRole("button", { name: REVIEW_CTA });
  await waitFor(() => {
    expect(cta).toBeEnabled();
  });
  await user.click(cta);
}

async function reachProvisionedTrader(
  user: ReturnType<typeof userEvent.setup>,
): Promise<string> {
  await reachReadyState(user);
  harness.list = () =>
    Promise.resolve(
      json({
        schema: "paper_trader_list.v1",
        count: 1,
        traders: [traderBody(createdRequestIds()[0])],
      }),
    );
  await user.click(screen.getByRole("button", { name: "建立交易員" }));
  await user.click(
    await screen.findByRole("button", { name: "確認並建立交易員" }),
  );
  await screen.findByRole("region", { name: "交易員詳情" });
  return createdRequestIds()[0];
}

describe("Stage B — ledger evidence", () => {
  it("requests the ledger only after an exact trader detail, at the exact path", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await screen.findByText("初始帳戶記錄");

    const ledgerIndex = harness.calls.findIndex((call) =>
      call.url.includes("/ledger-origin"),
    );
    const detailIndex = harness.calls.findIndex(
      (call) =>
        call.url.includes("/api/v1/paper/traders/") &&
        !call.url.includes("/ledger-origin"),
    );
    expect(detailIndex).toBeGreaterThanOrEqual(0);
    expect(ledgerIndex).toBeGreaterThan(detailIndex);
    const ledgerCall = harness.calls[ledgerIndex];
    expect(ledgerCall.method).toBe("GET");
    expect(ledgerCall.url).toBe(
      `/api/v1/paper/traders/${TRADER_ID}/ledger-origin`,
    );
    expect(ledgerCall.body).toBeNull();
  });

  it("renders the approved evidence order and only backend values", async () => {
    harness.ledger = () => Promise.resolve(json(ledgerBody(250000.5)));
    harness.baselines = () => Promise.resolve(json(baselinesBody(250000.5)));
    harness.create = (body) => {
      const record = traderBody((body as { request_id: string }).request_id);
      record.account.initial_capital = 250000.5;
      return Promise.resolve(json(record, 201));
    };
    harness.detail = () => {
      const record = traderBody(createdRequestIds()[0] ?? "");
      record.account.initial_capital = 250000.5;
      return Promise.resolve(json(record));
    };
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    harness.list = () => {
      const record = traderBody(createdRequestIds()[0]);
      record.account.initial_capital = 250000.5;
      return Promise.resolve(
        json({ schema: "paper_trader_list.v1", count: 1, traders: [record] }),
      );
    };
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );
    const panel = await screen.findByRole("region", { name: "交易員詳情" });
    await screen.findByText("初始帳戶記錄");

    const headings = within(panel)
      .getAllByRole("heading")
      .map((node) => node.textContent);
    // Runtime panel is present; Stage A detail blocks follow. Exact runtime
    // sub-headings depend on live snapshot load (often fail-closed without v4).
    expect(headings[0]).toBe(`${STRATEGY_ID} · ${CONTRACT_ID}`);
    expect(headings).toContain("模擬 runtime（IB 行情驅動嘅 app 自家模擬）");
    expect(headings).toContain("建立嗰刻嘅開始前檢查");
    expect(headings).toContain("初始帳戶記錄");
    expect(headings).toContain("鎖定對照基準嘅原始證據");
    expect(headings).toContain("帶偏離證據返 Terminal");
    expect(headings).toContain("模擬引擎尚未啟用。");

    // Money and counts come from the response, never from a frontend constant.
    expect(within(panel).getAllByText(/USD 250,000.5/).length).toBeGreaterThan(0);
    expect(within(panel).queryByText(/USD 100,000/)).not.toBeInTheDocument();
    expect(within(panel).getByText("0 ／ 1 ／ 4")).toBeInTheDocument();
    expect(within(panel).getByText("0 ／ 0")).toBeInTheDocument();
    expect(
      within(panel).getByText(/對照基準有 14 筆未成交記錄/),
    ).toBeInTheDocument();
    expect(
      within(panel).getByText("最接近觸發：rejection_000014"),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/唔等於「冇偏離」/)).toBeInTheDocument();
    expect(within(panel).getByText("baseline/result.json")).toBeInTheDocument();
    expect(within(panel).queryByText(/沒有偏離/)).not.toBeInTheDocument();
  });

  it("keeps the review action enabled for a valid zero-trade ledger", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    expect(await screen.findByRole("button", { name: REVIEW_CTA })).toBeEnabled();
  });

  it("keeps the review card visible and disabled when the ledger is missing", async () => {
    harness.ledger = () =>
      Promise.resolve(reviewError("ledger_not_ready", 409, null));
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);

    const cta = await screen.findByRole("button", { name: REVIEW_CTA });
    expect(cta).toBeInTheDocument();
    expect(cta).toBeDisabled();
    expect(
      screen.getAllByText(/未有已保存嘅初始帳戶記錄/).length,
    ).toBeGreaterThan(0);
    expect(countCalls("/review-snapshots")).toBe(0);
  });

  it("fails closed on a 503, an unknown body and a strict parser failure", async () => {
    for (const response of [
      () => Promise.resolve(reviewError("ledger_integrity_failed", 503, null)),
      () => Promise.resolve(json({ unexpected: true }, 500)),
      () => Promise.resolve(json({ schema: "paper_ledger_origin.v1" })),
    ]) {
      harness.ledger = response;
      const user = userEvent.setup();
      const view = renderPage();
      await reachProvisionedTrader(user);
      expect(
        await screen.findByRole("button", { name: REVIEW_CTA }),
      ).toBeDisabled();
      expect(countCalls("/review-snapshots")).toBe(0);
      view.unmount();
      harness.calls.length = 0;
    }
  });

  it("refuses a cross-trader ledger and a drifted locked identity", async () => {
    harness.ledger = () =>
      Promise.resolve(
        json(ledgerBody(100000, "trader-00000000000000000000000000000000")),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    expect(
      await screen.findByRole("button", { name: REVIEW_CTA }),
    ).toBeDisabled();
    expect(
      screen.getAllByText(/未能通過完整驗證|對唔上/).length,
    ).toBeGreaterThan(0);
  });

  it("refuses a ledger whose baseline drifts from the trader record", async () => {
    harness.ledger = () => {
      const body = ledgerBody();
      body.baseline.result_sha256 = "a".repeat(64);
      body.baseline.members[0].sha256 = "a".repeat(64);
      return Promise.resolve(json(body));
    };
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await waitFor(() => {
      expect(
        screen.getAllByText(/同呢個交易員嘅鎖定內容對唔上/).length,
      ).toBeGreaterThan(0);
    });
    expect(
      await screen.findByRole("button", { name: REVIEW_CTA }),
    ).toBeDisabled();
  });

  it("drops a ledger response that belongs to a superseded tab", async () => {
    const traderA = traderBody(RECOVERY_REQUEST_ID);
    const traderB = traderBody(RECOVERY_REQUEST_ID);
    traderB.trader_id = TRADER_ID_B;
    traderB.contract_id = CONTRACT_ID_B;
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 2,
          traders: [traderA, traderB],
        }),
      );
    harness.detail = (url) =>
      Promise.resolve(json(url.includes(TRADER_ID_B) ? traderB : traderA));

    const slowA = deferred<Response>();
    harness.ledger = (url) => {
      if (url.includes(TRADER_ID_B)) {
        const body = ledgerBody(100000, TRADER_ID_B);
        body.contract.contract_id = CONTRACT_ID_B;
        return Promise.resolve(json(body));
      }
      return slowA.promise;
    };

    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("tab", {
        name: `${STRATEGY_ID} · ${CONTRACT_ID}`,
      }),
    );
    await user.click(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID_B}` }),
    );
    const panel = await screen.findByRole("region", { name: "交易員詳情" });
    await screen.findByText("初始帳戶記錄");

    /*
     * The superseded tab finally answers with a body that is perfectly valid
     * for trader A, so only the tab authority can stop it landing on B.
     */
    await act(async () => {
      const staleBody = ledgerBody();
      staleBody.baseline.rejection_count = 7;
      slowA.resolve(json(staleBody));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(
      within(panel).queryByText(/對照基準有 7 筆未成交記錄/),
    ).not.toBeInTheDocument();
    expect(
      within(panel).getByText(/對照基準有 14 筆未成交記錄/),
    ).toBeInTheDocument();
  });

  it("shows no money or counts on the overview and no review control there", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await user.click(screen.getByRole("tab", { name: "總覽" }));

    const overview = await screen.findByRole("region", { name: "模擬盤總覽" });
    expect(within(overview).getByText("模擬引擎尚未啟用。")).toBeInTheDocument();
    expect(within(overview).queryByText(/0 筆/)).not.toBeInTheDocument();
    expect(within(overview).queryByText(/持倉/)).not.toBeInTheDocument();
    expect(
      within(overview).queryByRole("button", { name: /review|匯出|偏離/ }),
    ).not.toBeInTheDocument();
  });
});

describe("Stage B — review request POST", () => {
  it("writes the URL identity before the POST leaves", async () => {
    const gate = deferred<Response>();
    harness.reviewCreate = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    await waitFor(() => {
      expect(countCalls("/review-snapshots")).toBe(1);
    });
    const requestId = postedReviewRequestIds()[0];
    const call = harness.calls.find((row) =>
      row.url.includes("/review-snapshots"),
    );
    // The URL already carried both halves at the moment the POST left.
    expect(call?.search).toContain(`paper_trader=${TRADER_ID}`);
    expect(call?.search).toContain(`review_request=${requestId}`);
    expect(call?.method).toBe("POST");
    expect(call?.url).toBe(
      `/api/v1/paper/traders/${TRADER_ID}/review-snapshots`,
    );
    expect(Object.keys(call?.body as Record<string, unknown>).sort()).toEqual([
      "request_id",
      "schema",
    ]);

    gate.resolve(json(reviewStatusBody(requestId, "preparing"), 202));
    await screen.findByText(/準備緊：已完成/);
  });

  it("sends exactly one POST and one UUID for a same-batch triple click", async () => {
    const gate = deferred<Response>();
    harness.reviewCreate = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    const cta = await screen.findByRole("button", { name: REVIEW_CTA });

    await act(async () => {
      cta.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      cta.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      cta.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitFor(() => {
      expect(countCalls("/review-snapshots")).toBe(1);
    });
    expect(new Set(postedReviewRequestIds()).size).toBe(1);

    gate.resolve(
      json(reviewStatusBody(postedReviewRequestIds()[0], "preparing"), 202),
    );
    await screen.findByText(/準備緊：已完成/);
    expect(countCalls("/review-snapshots")).toBe(1);
  });

  it("locks the trader tabs while the request has no terminal outcome", async () => {
    const gate = deferred<Response>();
    harness.reviewCreate = () => gate.promise;
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await waitFor(() => {
      expect(countCalls("/review-snapshots")).toBe(1);
    });

    const overviewTab = screen.getByRole("tab", { name: "總覽" });
    expect(overviewTab).toBeDisabled();
    await act(async () => {
      overviewTab.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(
      screen.getByRole("region", { name: "交易員詳情" }),
    ).toBeInTheDocument();

    gate.resolve(
      json(reviewStatusBody(postedReviewRequestIds()[0], "ready")),
    );
    await screen.findByText("偏離包已經準備好。");
    expect(screen.getByRole("tab", { name: "總覽" })).toBeEnabled();
  });

  it("never retries after a transport unknown and never mints a second identity", async () => {
    harness.reviewCreate = () => Promise.reject(new TypeError("network down"));
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    expect(
      await screen.findByText(/系統唔會自己重試，亦唔會建立第二份/),
    ).toBeInTheDocument();
    expect(countCalls("/review-snapshots")).toBe(1);
    expect(new Set(postedReviewRequestIds()).size).toBe(1);
  });

  it("fails closed on an unknown error body without a second POST", async () => {
    harness.reviewCreate = () => Promise.resolve(json({ oops: true }, 500));
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    expect(
      await screen.findByText(/系統唔會自己重試，亦唔會建立第二份/),
    ).toBeInTheDocument();
    expect(countCalls("/review-snapshots")).toBe(1);
    expect(screen.queryByText("偏離包已經準備好。")).not.toBeInTheDocument();
  });
});

describe("Stage B — reload and status recovery", () => {
  function recoveryEntry(
    traderId = TRADER_ID,
    requestId = RECOVERY_REQUEST_ID,
  ): string {
    return `/paper?paper_trader=${traderId}&review_request=${requestId}`;
  }

  it("recovers the same request from the URL with zero POST and zero new identity", async () => {
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(RECOVERY_REQUEST_ID)],
        }),
      );
    harness.detail = () =>
      Promise.resolve(json(traderBody(RECOVERY_REQUEST_ID)));
    renderPage(recoveryEntry());

    await screen.findByText(/準備緊：已完成/);
    expect(countCalls("/review-snapshots")).toBe(0);
    const statusCalls = harness.calls.filter((call) =>
      call.url.includes("/api/v1/paper/review-requests/"),
    );
    expect(statusCalls).toHaveLength(1);
    expect(statusCalls[0].method).toBe("GET");
    expect(statusCalls[0].url).toBe(
      `/api/v1/paper/review-requests/${RECOVERY_REQUEST_ID}`,
    );
    // The status GET followed the trader detail, never preceded it.
    const detailIndex = harness.calls.findIndex(
      (call) =>
        call.url.includes("/api/v1/paper/traders/") &&
        !call.url.includes("/ledger-origin"),
    );
    expect(harness.calls.indexOf(statusCalls[0])).toBeGreaterThan(detailIndex);
  });

  it("fails closed on a missing half, an invalid id and a duplicate authority", async () => {
    for (const entry of [
      `/paper?paper_trader=${TRADER_ID}`,
      `/paper?review_request=${RECOVERY_REQUEST_ID}`,
      `/paper?paper_trader=../escape&review_request=${RECOVERY_REQUEST_ID}`,
      `/paper?paper_trader=${TRADER_ID}&review_request=not-a-uuid`,
      `/paper?paper_trader=${TRADER_ID}&paper_trader=${TRADER_ID}&review_request=${RECOVERY_REQUEST_ID}`,
    ]) {
      const view = renderPage(entry);
      await screen.findByRole("heading", { name: "模擬盤" });
      expect(countCalls("/api/v1/paper/review-requests/")).toBe(0);
      expect(countCalls("/review-snapshots")).toBe(0);
      view.unmount();
      harness.calls.length = 0;
    }
  });

  it("refuses a status whose trader is not the recovered trader", async () => {
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(RECOVERY_REQUEST_ID)],
        }),
      );
    harness.detail = () =>
      Promise.resolve(json(traderBody(RECOVERY_REQUEST_ID)));
    harness.reviewStatus = () => {
      const body = reviewStatusBody(RECOVERY_REQUEST_ID, "preparing");
      body.trader_id = "trader-00000000000000000000000000000000";
      return Promise.resolve(json(body));
    };
    renderPage(recoveryEntry());

    expect(
      await screen.findByText(/今次請求嘅結果未知/),
    ).toBeInTheDocument();
    expect(countCalls("/review-snapshots")).toBe(0);
  });

  it("rechecks a failed request with a GET only, and needs an explicit new snapshot", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody((body as { request_id: string }).request_id, "failed"),
          200,
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText(/最後已核實：7 ／ 10 部分/);

    const firstRequestId = postedReviewRequestIds()[0];
    harness.reviewStatus = () =>
      Promise.resolve(json(reviewStatusBody(firstRequestId, "failed")));
    await user.click(screen.getByRole("button", { name: "重新檢查狀態" }));
    await waitFor(() => {
      expect(countCalls("/api/v1/paper/review-requests/")).toBe(1);
    });
    expect(countCalls("/review-snapshots")).toBe(1);

    // A brand new snapshot needs an explicit action, a fresh identity and a POST.
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody(
            (body as { request_id: string }).request_id,
            "preparing",
          ),
          202,
        ),
      );
    await user.click(screen.getByRole("button", { name: "建立新嘅偏離包" }));
    await waitFor(() => {
      expect(countCalls("/review-snapshots")).toBe(2);
    });
    const ids = postedReviewRequestIds();
    expect(new Set(ids).size).toBe(2);
    const secondPost = harness.calls.filter((call) =>
      call.url.includes("/review-snapshots"),
    )[1];
    expect(secondPost.search).toContain(`review_request=${ids[1]}`);
  });

  it("drops a stale status response for a superseded request", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody((body as { request_id: string }).request_id, "failed"),
          200,
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText(/最後已核實：7 ／ 10 部分/);

    const firstRequestId = postedReviewRequestIds()[0];
    const slow = deferred<Response>();
    harness.reviewStatus = () => slow.promise;
    await user.click(screen.getByRole("button", { name: "重新檢查狀態" }));

    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody(
            (body as { request_id: string }).request_id,
            "preparing",
          ),
          202,
        ),
      );
    await user.click(screen.getByRole("button", { name: "建立新嘅偏離包" }));
    await screen.findByText(/準備緊：已完成/);

    // The old recheck finally answers "ready" for the superseded request.
    await act(async () => {
      slow.resolve(json(reviewStatusBody(firstRequestId, "ready")));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByText("偏離包已經準備好。")).not.toBeInTheDocument();
    expect(screen.getByText(/準備緊：已完成/)).toBeInTheDocument();
  });

  it("shows the ready truth and offers the controls without calling anything", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody((body as { request_id: string }).request_id, "ready"),
          201,
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    expect(await screen.findByText("偏離包已經準備好。")).toBeInTheDocument();
    expect(screen.getByText(SNAPSHOT_ID)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "下載偏離包" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "複製 Terminal 開場白" }),
    ).toBeInTheDocument();
    // Offering a control is not using it: nothing is fetched until a click.
    expect(countCalls("/download")).toBe(0);
    expect(countCalls("/terminal-opener")).toBe(0);
    expect(browser.objectUrls).toHaveLength(0);
    expect(browser.copied).toHaveLength(0);
  });
});

/*
 * Correction A ([226]): the app entry renders <StrictMode>, which replays
 * effect setup → cleanup → setup on mount. Every reload test below uses that
 * exact production boundary; a plain mount never exercises the cleanup.
 */
describe("Correction A — production StrictMode reload recovery", () => {
  const ENTRY = `?paper_trader=${TRADER_ID}&review_request=${RECOVERY_REQUEST_ID}`;

  function renderStrictPage(search: string) {
    currentSearch = "";
    return render(
      <StrictMode>
        <MemoryRouter initialEntries={[`/paper${search}`]}>
          <PaperPage />
          <LocationProbe />
        </MemoryRouter>
      </StrictMode>,
    );
  }

  /** The recovered trader is the exact one named by the URL identity. */
  function recoveredTrader() {
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(RECOVERY_REQUEST_ID)],
        }),
      );
    harness.detail = () =>
      Promise.resolve(json(traderBody(RECOVERY_REQUEST_ID)));
  }

  function statusOf(status: "preparing" | "ready" | "failed") {
    harness.reviewStatus = (url) =>
      Promise.resolve(json(reviewStatusBody(url.split("/").pop() ?? "", status)));
  }

  function statusCalls(): Call[] {
    return harness.calls.filter((call) =>
      call.url.includes("/api/v1/paper/review-requests/"),
    );
  }

  let uuidSpy: { mockRestore: () => void } | null = null;

  /** Any minted identity is a defect here: the URL already holds the only one. */
  function watchUuid() {
    const spy = vi.spyOn(globalThis.crypto, "randomUUID");
    uuidSpy = spy;
    return spy;
  }

  afterEach(() => {
    uuidSpy?.mockRestore();
    uuidSpy = null;
  });

  it("recovers a preparing request across setup, cleanup and setup", async () => {
    recoveredTrader();
    statusOf("preparing");
    const uuid = watchUuid();
    renderStrictPage(ENTRY);

    /*
     * Counts are asserted before the screen so a regression is recorded as
     * "the GET left and the answer was dropped", not merely as a timeout.
     */
    await waitFor(() => {
      expect(statusCalls()).toHaveLength(1);
    });
    expect(countCalls("/review-snapshots")).toBe(0);
    expect(uuid).not.toHaveBeenCalled();
    const gets = statusCalls();
    expect(gets).toHaveLength(1);
    expect(gets[0].method).toBe("GET");
    expect(gets[0].url).toBe(
      `/api/v1/paper/review-requests/${RECOVERY_REQUEST_ID}`,
    );
    const detailIndex = harness.calls.findIndex(
      (call) =>
        call.url.includes(`/api/v1/paper/traders/${TRADER_ID}`) &&
        !call.url.includes("/ledger-origin"),
    );
    expect(detailIndex).toBeGreaterThanOrEqual(0);
    expect(harness.calls.indexOf(gets[0])).toBeGreaterThan(detailIndex);
    // Neither half of the identity was rewritten or repaired.
    expect(currentSearch).toBe(ENTRY);

    expect(
      await screen.findByText("準備緊：已完成 4 ／ 10 部分。"),
    ).toBeInTheDocument();
    expect(screen.getByText(/擷取時間：/)).toBeInTheDocument();
    // The generic "still preparing" line is the stuck state C-v5 proved.
    expect(screen.queryByText("準備緊今次偏離包…")).not.toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID}` }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("recovers a failed request and unlocks the tabs", async () => {
    recoveredTrader();
    statusOf("failed");
    const uuid = watchUuid();
    renderStrictPage(ENTRY);

    expect(
      await screen.findByText("偏離包嘅完整性核對唔通過。"),
    ).toBeInTheDocument();
    expect(screen.getByText("最後已核實：7 ／ 10 部分。")).toBeInTheDocument();
    expect(screen.queryByText("準備緊今次偏離包…")).not.toBeInTheDocument();
    expect(countCalls("/review-snapshots")).toBe(0);
    expect(uuid).not.toHaveBeenCalled();
    expect(statusCalls()).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "總覽" })).toBeEnabled();
  });

  it("recovers a ready request with no download, opener or blob", async () => {
    recoveredTrader();
    statusOf("ready");
    const uuid = watchUuid();
    renderStrictPage(ENTRY);

    expect(await screen.findByText("偏離包已經準備好。")).toBeInTheDocument();
    expect(screen.getByText(SNAPSHOT_ID)).toBeInTheDocument();
    expect(screen.getByText(RECOVERY_REQUEST_ID)).toBeInTheDocument();
    expect(countCalls("/review-snapshots")).toBe(0);
    expect(uuid).not.toHaveBeenCalled();
    expect(statusCalls()).toHaveLength(1);
    // The recovered dialog offers the controls but has not used them.
    expect(
      screen.getByRole("button", { name: "下載偏離包" }),
    ).toBeInTheDocument();
    expect(countCalls("/download")).toBe(0);
    expect(countCalls("/terminal-opener")).toBe(0);
    expect(browser.objectUrls).toHaveLength(0);
    expect(browser.copied).toHaveLength(0);
    expect(screen.getByRole("tab", { name: "總覽" })).toBeEnabled();
  });

  it("keeps every invalid URL at zero GET, zero POST and zero identity", async () => {
    recoveredTrader();
    statusOf("preparing");
    for (const search of [
      `?paper_trader=${TRADER_ID}`,
      `?review_request=${RECOVERY_REQUEST_ID}`,
      `?paper_trader=../escape&review_request=${RECOVERY_REQUEST_ID}`,
      `?paper_trader=${TRADER_ID}&review_request=not-a-uuid`,
      `?paper_trader=${TRADER_ID}&paper_trader=${TRADER_ID}&review_request=${RECOVERY_REQUEST_ID}`,
    ]) {
      const uuid = watchUuid();
      const view = renderStrictPage(search);
      await screen.findByRole("heading", { name: "模擬盤" });
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(countCalls("/api/v1/paper/review-requests/")).toBe(0);
      expect(countCalls("/review-snapshots")).toBe(0);
      expect(uuid).not.toHaveBeenCalled();
      // Nothing was repaired, completed or dropped from the query either.
      expect(currentSearch).toBe(search);
      view.unmount();
      harness.calls.length = 0;
      uuid.mockRestore();
      uuidSpy = null;
    }
  });

  it("writes nothing after a real unmount and logs no error", async () => {
    recoveredTrader();
    const gate = deferred<Response>();
    harness.reviewStatus = () => gate.promise;
    const view = renderStrictPage(ENTRY);
    await waitFor(() => {
      expect(statusCalls()).toHaveLength(1);
    });
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    screen.getByText("準備緊今次偏離包…");
    view.unmount();

    await act(async () => {
      gate.resolve(json(reviewStatusBody(RECOVERY_REQUEST_ID, "ready")));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(errors).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("偏離包已經準備好。");
    errors.mockRestore();
  });

  it("keeps a pending recheck from overwriting an explicit new request", async () => {
    recoveredTrader();
    statusOf("failed");
    const uuid = watchUuid();
    const user = userEvent.setup();
    renderStrictPage(ENTRY);
    await screen.findByText("偏離包嘅完整性核對唔通過。");
    expect(uuid).not.toHaveBeenCalled();

    const slow = deferred<Response>();
    harness.reviewStatus = () => slow.promise;
    await user.click(screen.getByRole("button", { name: "重新檢查狀態" }));
    await waitFor(() => {
      expect(statusCalls()).toHaveLength(2);
    });

    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody(
            (body as { request_id: string }).request_id,
            "preparing",
          ),
          202,
        ),
      );
    await user.click(screen.getByRole("button", { name: "建立新嘅偏離包" }));
    await screen.findByText("準備緊：已完成 4 ／ 10 部分。");
    const posted = postedReviewRequestIds();
    expect(posted).toHaveLength(1);
    expect(posted[0]).not.toBe(RECOVERY_REQUEST_ID);
    expect(currentSearch).toContain(`review_request=${posted[0]}`);

    // The superseded recheck finally answers "ready" for the old identity.
    await act(async () => {
      slow.resolve(json(reviewStatusBody(RECOVERY_REQUEST_ID, "ready")));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByText("偏離包已經準備好。")).not.toBeInTheDocument();
    expect(
      screen.getByText("準備緊：已完成 4 ／ 10 部分。"),
    ).toBeInTheDocument();
  });

  it("leaves no permanent status lock after a recovered failure", async () => {
    recoveredTrader();
    statusOf("failed");
    const user = userEvent.setup();
    renderStrictPage(ENTRY);
    await screen.findByText("偏離包嘅完整性核對唔通過。");

    await user.click(screen.getByRole("button", { name: "重新檢查狀態" }));
    await waitFor(() => {
      expect(statusCalls()).toHaveLength(2);
    });
    await user.click(screen.getByRole("button", { name: "重新檢查狀態" }));
    await waitFor(() => {
      expect(statusCalls()).toHaveLength(3);
    });
    for (const call of statusCalls()) {
      expect(call.method).toBe("GET");
      expect(call.url).toBe(
        `/api/v1/paper/review-requests/${RECOVERY_REQUEST_ID}`,
      );
    }
    expect(countCalls("/review-snapshots")).toBe(0);
  });
});

/*
 * Phase 3 ([229]): the ready dialog, the download verification pipeline and
 * the persisted Terminal opener. Mock transport only — no server, no browser,
 * no real download, no real clipboard and no default P6 database.
 */
describe("Phase 3 — review dialog lifecycle", () => {
  async function reachPreparing(user: ReturnType<typeof userEvent.setup>) {
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("準備緊：已完成 4 ／ 10 部分。");
  }

  async function reachReady(user: ReturnType<typeof userEvent.setup>) {
    const artifact = await ensureArtifact();
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    return artifact;
  }

  it("opens a real dialog for preparing and keeps the request when it closes", async () => {
    const user = userEvent.setup();
    await reachPreparing(user);

    const dialog = screen.getByRole("dialog", { name: "偏離包" });
    expect(within(dialog).getByText(/準備緊：已完成/)).toBeInTheDocument();
    // Ready-only controls must not exist in the DOM before ready.
    expect(
      within(dialog).queryByRole("button", { name: "下載偏離包" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "複製 Terminal 開場白" }),
    ).not.toBeInTheDocument();

    const searchBefore = currentSearch;
    const callsBefore = harness.calls.length;
    await user.click(within(dialog).getByRole("button", { name: "關閉" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("偏離包狀態：準備緊")).toBeInTheDocument();
    expect(currentSearch).toBe(searchBefore);
    expect(harness.calls).toHaveLength(callsBefore);
    expect(countCalls("/review-snapshots")).toBe(1);
    // Closing is presentation only: the trader stays locked.
    expect(screen.getByRole("tab", { name: "總覽" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "查看偏離包狀態" }));
    expect(
      await screen.findByRole("dialog", { name: "偏離包" }),
    ).toBeInTheDocument();
    expect(harness.calls).toHaveLength(callsBefore);
    expect(postedReviewRequestIds()).toHaveLength(1);
    expect(currentSearch).toBe(searchBefore);
  });

  it("unlocks the trader once the outcome is terminal", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    expect(screen.getByRole("tab", { name: "總覽" })).toBeEnabled();
    expect(screen.getByText("偏離包狀態：已經準備好")).toBeInTheDocument();
  });

  it("keeps the failed and unknown dialogs free of any artifact control", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody((body as { request_id: string }).request_id, "failed"),
          200,
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    const dialog = await screen.findByRole("dialog", { name: "偏離包" });
    expect(
      within(dialog).getByText("偏離包嘅完整性核對唔通過。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "下載偏離包" }),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole("button", { name: "複製 Terminal 開場白" }),
    ).not.toBeInTheDocument();
    expect(countCalls("/download")).toBe(0);
    expect(countCalls("/terminal-opener")).toBe(0);
  });

  it("shows the failed diagnostics the backend proved, and invents nothing", async () => {
    harness.reviewCreate = (body) => {
      const requestId = (body as { request_id: string }).request_id;
      const failed = reviewStatusBody(requestId, "failed");
      const error = failed.error;
      if (error === null) {
        throw new Error("failed fixture missing");
      }
      return Promise.resolve(
        json(
          {
            ...failed,
            error: {
              ...error,
              issues: [
                {
                  kind: "missing_member",
                  path: `baseline/events/${RUN_ID}.json`,
                  source_ref: "baseline/result.json#events_ref",
                  expected_sha256: null,
                  actual_sha256: null,
                  ref_chain: ["paper-review.json", "baseline/result.json"],
                },
                {
                  kind: "hash_mismatch",
                  path: "paper/equity.json",
                  source_ref: null,
                  expected_sha256: RESULT_SHA,
                  actual_sha256: CONTENT_SHA,
                  ref_chain: [],
                },
              ],
            },
          },
          200,
        ),
      );
    };
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    const dialog = await screen.findByRole("dialog", { name: "偏離包" });
    expect(
      within(dialog).getByText(`缺少檔案：baseline/events/${RUN_ID}.json`),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("來源：baseline/result.json#events_ref"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        `應該係 ${RESULT_SHA}；而家係 ${CONTENT_SHA}`,
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "追溯：paper-review.json → baseline/result.json",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/最後已核實：7 ／ 10 部分/)).toBeInTheDocument();
  });

  it("shows every ready field and the ten members in the stored order", async () => {
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    const dialog = screen.getByRole("dialog", { name: "偏離包" });

    expect(within(dialog).getByText(SNAPSHOT_ID)).toBeInTheDocument();
    expect(
      within(dialog).getByText(postedReviewRequestIds()[0]),
    ).toBeInTheDocument();
    // The UTC instant is shown exactly as the backend wrote it.
    expect(within(dialog).getByText(AT)).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        new RegExp(Intl.DateTimeFormat().resolvedOptions().timeZone),
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/已建立；模擬引擎尚未啟用（provisioned）/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("not_evaluable · engine_not_enabled"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText(NOT_EVALUABLE)).toBeInTheDocument();
    expect(within(dialog).getByText(READY_FILENAME)).toBeInTheDocument();
    expect(
      within(dialog).getByText(`${String(artifact.artifact_bytes)} bytes`),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(artifact.artifact_sha256),
    ).toBeInTheDocument();
    expect(artifact.artifact_sha256).toMatch(/^[0-9a-f]{64}$/);

    const items = within(dialog)
      .getAllByRole("listitem")
      .map((node) => node.textContent ?? "");
    const memberItems = items.filter((text) => text.includes("bytes ·"));
    expect(memberItems).toHaveLength(10);
    memberItems.forEach((text, index) => {
      const member = artifact.members[index];
      expect(text).toContain(`${String(index + 1)}. ${member.path}`);
      expect(text).toContain(`${String(member.bytes)} bytes`);
      expect(text).toContain(member.sha256);
    });
  });

  it("reads the ledger summary from the backend, never from the page", async () => {
    const capital = 250_000;
    const artifact = await ensureArtifact();
    const detailBody = (requestId: string) => {
      const body = traderBody(requestId);
      body.account.initial_capital = capital;
      return body;
    };
    harness.baselines = () => Promise.resolve(json(baselinesBody(capital)));
    harness.create = (body) =>
      Promise.resolve(
        json(detailBody((body as { request_id: string }).request_id), 201),
      );
    harness.detail = () =>
      Promise.resolve(json(detailBody(createdRequestIds()[0] ?? "")));
    harness.ledger = () => Promise.resolve(json(ledgerBody(capital)));
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await reachReadyState(user);
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [detailBody(createdRequestIds()[0])],
        }),
      );
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );
    await screen.findByRole("region", { name: "交易員詳情" });
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");

    const dialog = screen.getByRole("dialog", { name: "偏離包" });
    // Every number tracks the fixture: nothing here is written into the page.
    expect(
      within(dialog).getByText("USD 250,000 ／ USD 250,000"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("USD 0 ／ USD 0")).toBeInTheDocument();
    expect(within(dialog).getByText("0 ／ 0")).toBeInTheDocument();
    expect(within(dialog).queryByText("USD 100,000")).not.toBeInTheDocument();
  });
});

describe("Phase 3 — download verification", () => {
  async function reachReady(user: ReturnType<typeof userEvent.setup>) {
    const artifact = await ensureArtifact();
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    return artifact;
  }

  function downloadButton() {
    return screen.getByRole("button", { name: "下載偏離包" });
  }

  it("verifies the whole artifact before it saves anything", async () => {
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    await user.click(downloadButton());
    await screen.findByText(`已經下載：${READY_FILENAME}`);

    expect(countCalls("/download")).toBe(1);
    const call = harness.calls.find((row) => row.url.includes("/download"));
    expect(call?.method).toBe("GET");
    expect(call?.url).toBe(
      `/api/v1/paper/review-snapshots/${SNAPSHOT_ID}/download`,
    );
    expect(browser.objectUrls).toHaveLength(1);
    expect(browser.objectUrls[0].type).toBe("application/zip");
    expect(browser.objectUrls[0].size).toBe(artifact.artifact_bytes);
    expect(browser.anchorClicks).toHaveLength(1);
    expect(browser.anchorClicks[0].download).toBe(READY_FILENAME);
    expect(browser.anchorClicks[0].href).toBe(browser.objectUrls[0].url);
    expect(browser.anchorClicks[0].connected).toBe(true);
    expect(browser.revoked).toEqual([browser.objectUrls[0].url]);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(browser.storageWrites).toBe(0);
  });

  it("sends exactly one GET for a same-batch triple click", async () => {
    const gate = deferred<Response>();
    const user = userEvent.setup();
    await reachReady(user);
    harness.reviewDownload = () => gate.promise;
    const button = downloadButton();

    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(countCalls("/download")).toBe(1);

    gate.resolve(zipResponse((await ensureArtifact()).bytes));
    await screen.findByText(`已經下載：${READY_FILENAME}`);
    expect(countCalls("/download")).toBe(1);
    expect(browser.objectUrls).toHaveLength(1);
    expect(browser.anchorClicks).toHaveLength(1);
  });

  it("lets the Owner ask again explicitly, and never retries by itself", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    await user.click(downloadButton());
    await screen.findByText(`已經下載：${READY_FILENAME}`);
    expect(countCalls("/download")).toBe(1);

    await user.click(downloadButton());
    await waitFor(() => {
      expect(browser.objectUrls).toHaveLength(2);
    });
    expect(countCalls("/download")).toBe(2);
    expect(browser.revoked).toHaveLength(2);
    expect(browser.anchorClicks).toHaveLength(2);

    // No timer, no auto retry: the count stays put without another click.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(countCalls("/download")).toBe(2);
  });

  it("fails closed on a wrong status, type, disposition or filename", async () => {
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    const cases = [
      { name: "409", overrides: { status: 409 } },
      { name: "type", overrides: { contentType: "application/octet-stream" } },
      { name: "no type", overrides: { contentType: null } },
      { name: "no disposition", overrides: { contentDisposition: null } },
      {
        name: "other filename",
        overrides: { contentDisposition: 'attachment; filename="other.zip"' },
      },
      {
        name: "inline",
        overrides: {
          contentDisposition: `inline; filename="${READY_FILENAME}"`,
        },
      },
    ];
    for (const item of cases) {
      harness.reviewDownload = () =>
        Promise.resolve(zipResponse(artifact.bytes, item.overrides));
      await user.click(downloadButton());
      expect(
        await screen.findByText("偏離包下載唔到。", undefined, {
          timeout: 3000,
        }),
        item.name,
      ).toBeInTheDocument();
      expect(browser.objectUrls, item.name).toHaveLength(0);
      expect(browser.anchorClicks, item.name).toHaveLength(0);
      expect(screen.queryByText(/已經下載：/)).not.toBeInTheDocument();
    }
  });

  it("fails closed on drifted bytes, whole digest, order, count and member digest", async () => {
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    const entries = canonicalEntries();

    const reordered = [...entries];
    [reordered[1], reordered[2]] = [reordered[2], reordered[1]];
    const missing = entries.slice(0, 9);
    const extra = [
      ...entries,
      { path: "extra/eleventh.json", content: zipMemberContent("extra") },
    ];
    const duplicated = [...entries.slice(0, 9), { ...entries[0] }];
    const tampered = entries.map((entry, index) =>
      index === 4
        ? { path: entry.path, content: zipMemberContent(`${entry.path}!`) }
        : entry,
    );

    const cases: Array<[string, Uint8Array]> = [
      ["truncated", artifact.bytes.subarray(0, artifact.bytes.byteLength - 4)],
      ["one byte short", artifact.bytes.subarray(1)],
      ["reordered", buildStoredZip(reordered)],
      ["missing member", buildStoredZip(missing)],
      ["extra member", buildStoredZip(extra)],
      ["duplicated member", buildStoredZip(duplicated)],
      ["tampered member", buildStoredZip(tampered)],
    ];
    for (const [name, bytes] of cases) {
      harness.reviewDownload = () => Promise.resolve(zipResponse(bytes));
      await user.click(downloadButton());
      await waitFor(() => {
        expect(
          screen.getByText(/下載到嘅偏離包內容核對唔通過|偏離包下載唔到/),
          name,
        ).toBeInTheDocument();
      });
      expect(browser.objectUrls, name).toHaveLength(0);
      expect(browser.anchorClicks, name).toHaveLength(0);
      expect(screen.queryByText(/已經下載：/), name).not.toBeInTheDocument();
    }
  });

  it("drops a download answer that belongs to a superseded trader", async () => {
    const artifact = await ensureArtifact();
    const traderA = traderBody(RECOVERY_REQUEST_ID);
    const traderB = traderBody(RECOVERY_REQUEST_ID);
    traderB.trader_id = TRADER_ID_B;
    traderB.contract_id = CONTRACT_ID_B;
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 2,
          traders: [traderA, traderB],
        }),
      );
    harness.detail = (url) =>
      Promise.resolve(json(url.includes(TRADER_ID_B) ? traderB : traderA));
    harness.ledger = (url) => {
      if (url.includes(TRADER_ID_B)) {
        const body = ledgerBody(100000, TRADER_ID_B);
        body.contract.contract_id = CONTRACT_ID_B;
        return Promise.resolve(json(body));
      }
      return Promise.resolve(json(ledgerBody()));
    };
    harness.reviewStatus = () =>
      Promise.resolve(json(readyStatusFor(RECOVERY_REQUEST_ID, artifact)));
    const gate = deferred<Response>();
    harness.reviewDownload = () => gate.promise;

    const user = userEvent.setup();
    renderPage(
      `/paper?paper_trader=${TRADER_ID}&review_request=${RECOVERY_REQUEST_ID}`,
    );
    await screen.findByText("偏離包已經準備好。");
    await user.click(screen.getByRole("button", { name: "下載偏離包" }));
    expect(countCalls("/download")).toBe(1);

    await user.click(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID_B}` }),
    );
    await screen.findByText("初始帳戶記錄");

    await act(async () => {
      gate.resolve(zipResponse(artifact.bytes));
    });
    await settle();
    expect(browser.objectUrls).toHaveLength(0);
    expect(browser.anchorClicks).toHaveLength(0);
    expect(screen.queryByText(/已經下載：/)).not.toBeInTheDocument();
  });

  it("writes nothing after a real unmount", async () => {
    const gate = deferred<Response>();
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    harness.reviewDownload = () => gate.promise;
    await user.click(downloadButton());
    expect(countCalls("/download")).toBe(1);

    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    cleanup();
    await act(async () => {
      gate.resolve(zipResponse(artifact.bytes));
    });
    await settle();
    expect(browser.objectUrls).toHaveLength(0);
    expect(browser.anchorClicks).toHaveLength(0);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });
});

describe("Phase 3 — Terminal opener copy", () => {
  async function reachReady(user: ReturnType<typeof userEvent.setup>) {
    const artifact = await ensureArtifact();
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    return artifact;
  }

  function copyButton() {
    return screen.getByRole("button", { name: "複製 Terminal 開場白" });
  }

  it("copies the persisted backend text byte for byte", async () => {
    const user = userEvent.setup();
    const writeText = installClipboardProbe();
    await reachReady(user);
    await user.click(copyButton());
    await screen.findByText("已複製 Terminal 開場白。");

    expect(countCalls("/terminal-opener")).toBe(1);
    const call = harness.calls.find((row) =>
      row.url.includes("/terminal-opener"),
    );
    expect(call?.method).toBe("GET");
    expect(call?.url).toBe(
      `/api/v1/paper/review-snapshots/${SNAPSHOT_ID}/terminal-opener`,
    );
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(browser.copied).toHaveLength(1);
    expect(browser.copied[0]).toBe(OPENER_TEXT);
    expect(Array.from(new TextEncoder().encode(browser.copied[0]))).toEqual(
      Array.from(new TextEncoder().encode(OPENER_TEXT)),
    );
    expect(browser.storageWrites).toBe(0);
  });

  it("copies whatever the backend persisted, never a frontend template", async () => {
    const other = "完全唔同嘅一段 backend 文字 ${display_filename}\n";
    const encoded = new TextEncoder().encode(other);
    const user = userEvent.setup();
    installClipboardProbe();
    const artifact = await ensureArtifact();
    const otherSha = (await paperSha256Hex(encoded)) ?? "";
    harness.reviewCreate = (body) => {
      const base = readyStatusFor(
        (body as { request_id: string }).request_id,
        artifact,
      );
      return Promise.resolve(
        json(
          {
            ...base,
            ready: {
              ...base.ready,
              terminal_opener: {
                bytes: encoded.byteLength,
                sha256: otherSha,
              },
            },
          },
          201,
        ),
      );
    };
    harness.reviewOpener = async () =>
      json(
        await openerBody({
          text: other,
          bytes: encoded.byteLength,
          sha256: otherSha,
        }),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    await user.click(copyButton());
    await screen.findByText("已複製 Terminal 開場白。");

    expect(browser.copied).toEqual([other]);
    // The template slot is copied verbatim, never expanded by the page.
    expect(browser.copied[0]).toContain("${display_filename}");
  });

  it("sends exactly one GET and one copy for a same-batch double click", async () => {
    const gate = deferred<Response>();
    const user = userEvent.setup();
    installClipboardProbe();
    await reachReady(user);
    harness.reviewOpener = () => gate.promise;
    const button = copyButton();

    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(countCalls("/terminal-opener")).toBe(1);

    gate.resolve(json(await openerBody()));
    await screen.findByText("已複製 Terminal 開場白。");
    expect(countCalls("/terminal-opener")).toBe(1);
    expect(browser.copied).toHaveLength(1);
  });

  it("never copies when any claim fails", async () => {
    const user = userEvent.setup();
    installClipboardProbe();
    await reachReady(user);
    const encodedOther = new TextEncoder().encode("另一段文字\n");
    const cases: Array<[string, () => Promise<Response>]> = [
      [
        "other snapshot",
        async () =>
          json(
            await openerBody({
              snapshot_id: "paper-review-00000000000000000000000000000000",
            }),
          ),
      ],
      ["bytes drift", async () => json(await openerBody({ bytes: 4 }))],
      [
        "digest drift",
        async () =>
          json(
            await openerBody({
              sha256:
                "1111111111111111111111111111111111111111111111111111111111111111",
            }),
          ),
      ],
      [
        "text drift",
        async () =>
          json(
            await openerBody({
              text: "另一段文字\n",
              bytes: encodedOther.byteLength,
              sha256: (await paperSha256Hex(encodedOther)) ?? "",
            }),
          ),
      ],
      ["unknown body", () => Promise.resolve(json({ oops: true }))],
      [
        "known error",
        () =>
          Promise.resolve(reviewError("snapshot_not_found", 404, null)),
      ],
      ["transport", () => Promise.reject(new TypeError("network down"))],
    ];
    for (const [name, responder] of cases) {
      harness.reviewOpener = responder;
      await user.click(copyButton());
      await waitFor(() => {
        expect(
          screen.getByText(
            /Terminal 開場白核對唔通過|Terminal 開場白讀唔到|搵唔到今次偏離包/,
          ),
          name,
        ).toBeInTheDocument();
      });
      expect(browser.copied, name).toHaveLength(0);
      expect(
        screen.queryByText("已複製 Terminal 開場白。"),
        name,
      ).not.toBeInTheDocument();
    }
  });

  it("reports a clipboard refusal honestly instead of claiming success", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    const writeText = installClipboardProbe("reject");

    await user.click(copyButton());
    expect(
      await screen.findByText("複製唔到 Terminal 開場白。"),
    ).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByText("已複製 Terminal 開場白。"),
    ).not.toBeInTheDocument();
  });

  it("drops an opener answer that belongs to a superseded trader", async () => {
    const artifact = await ensureArtifact();
    const traderA = traderBody(RECOVERY_REQUEST_ID);
    const traderB = traderBody(RECOVERY_REQUEST_ID);
    traderB.trader_id = TRADER_ID_B;
    traderB.contract_id = CONTRACT_ID_B;
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 2,
          traders: [traderA, traderB],
        }),
      );
    harness.detail = (url) =>
      Promise.resolve(json(url.includes(TRADER_ID_B) ? traderB : traderA));
    harness.ledger = (url) => {
      if (url.includes(TRADER_ID_B)) {
        const body = ledgerBody(100000, TRADER_ID_B);
        body.contract.contract_id = CONTRACT_ID_B;
        return Promise.resolve(json(body));
      }
      return Promise.resolve(json(ledgerBody()));
    };
    harness.reviewStatus = () =>
      Promise.resolve(json(readyStatusFor(RECOVERY_REQUEST_ID, artifact)));
    const gate = deferred<Response>();
    harness.reviewOpener = () => gate.promise;

    const user = userEvent.setup();
    installClipboardProbe();
    renderPage(
      `/paper?paper_trader=${TRADER_ID}&review_request=${RECOVERY_REQUEST_ID}`,
    );
    await screen.findByText("偏離包已經準備好。");
    await user.click(copyButton());
    expect(countCalls("/terminal-opener")).toBe(1);

    await user.click(
      screen.getByRole("tab", { name: `${STRATEGY_ID} · ${CONTRACT_ID_B}` }),
    );
    await screen.findByText("初始帳戶記錄");

    const openerResponse = json(await openerBody());
    await act(async () => {
      gate.resolve(openerResponse);
    });
    await settle();
    expect(browser.copied).toHaveLength(0);
    expect(
      screen.queryByText("已複製 Terminal 開場白。"),
    ).not.toBeInTheDocument();
  });
});

/*
 * Correction A ([232]): the rendered `disabled` attribute is not the authority
 * for a click dispatched in the same batch, and a browser side effect that
 * throws must not leak an object URL or an anchor.
 */
describe("Correction A — same-batch and side-effect ownership", () => {
  async function reachReady(user: ReturnType<typeof userEvent.setup>) {
    const artifact = await ensureArtifact();
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    return artifact;
  }

  it("refuses a new snapshot dispatched in the same batch as a download", async () => {
    const gate = deferred<Response>();
    const user = userEvent.setup();
    const artifact = await reachReady(user);
    harness.reviewDownload = () => gate.promise;
    harness.reviewCreate = () => {
      throw new Error("a second create must never be attempted");
    };

    // POST only: the download and opener paths share the same URL prefix.
    const postsBefore = countCalls("/review-snapshots", "POST");
    const idsBefore = postedReviewRequestIds();
    const searchBefore = currentSearch;
    const download = screen.getByRole("button", { name: "下載偏離包" });
    const newSnapshot = screen.getByRole("button", {
      name: "建立新嘅偏離包",
    });
    // One batch, no re-render in between: the buttons are still enabled in the
    // DOM, so only the synchronous refs can stop the second handler.
    expect(newSnapshot).toBeEnabled();
    await act(async () => {
      download.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      newSnapshot.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(countCalls("/download")).toBe(1);
    expect(countCalls("/review-snapshots", "POST")).toBe(postsBefore);
    expect(postedReviewRequestIds()).toEqual(idsBefore);
    expect(new Set(postedReviewRequestIds()).size).toBe(1);
    expect(currentSearch).toBe(searchBefore);
    expect(screen.getByText(SNAPSHOT_ID)).toBeInTheDocument();

    // The download that was already in flight still finishes for that snapshot.
    gate.resolve(zipResponse(artifact.bytes));
    await screen.findByText(`已經下載：${READY_FILENAME}`);
    expect(countCalls("/download")).toBe(1);
    expect(browser.objectUrls).toHaveLength(1);
    expect(browser.anchorClicks[0].download).toBe(READY_FILENAME);
    expect(currentSearch).toBe(searchBefore);
  });

  it("refuses a new snapshot dispatched in the same batch as a copy", async () => {
    const gate = deferred<Response>();
    const user = userEvent.setup();
    installClipboardProbe();
    await reachReady(user);
    harness.reviewOpener = () => gate.promise;
    harness.reviewCreate = () => {
      throw new Error("a second create must never be attempted");
    };

    // POST only: the download and opener paths share the same URL prefix.
    const postsBefore = countCalls("/review-snapshots", "POST");
    const idsBefore = postedReviewRequestIds();
    const searchBefore = currentSearch;
    const copy = screen.getByRole("button", { name: "複製 Terminal 開場白" });
    const newSnapshot = screen.getByRole("button", {
      name: "建立新嘅偏離包",
    });
    expect(newSnapshot).toBeEnabled();
    await act(async () => {
      copy.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      newSnapshot.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(countCalls("/terminal-opener")).toBe(1);
    expect(countCalls("/review-snapshots", "POST")).toBe(postsBefore);
    expect(postedReviewRequestIds()).toEqual(idsBefore);
    expect(currentSearch).toBe(searchBefore);

    const openerResponse = json(await openerBody());
    await act(async () => {
      gate.resolve(openerResponse);
    });
    await screen.findByText("已複製 Terminal 開場白。");
    expect(countCalls("/terminal-opener")).toBe(1);
    expect(browser.copied).toEqual([OPENER_TEXT]);
    expect(currentSearch).toBe(searchBefore);
  });

  it("cleans up and fails honestly when the browser refuses the click", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      throw new Error("click refused");
    });

    await user.click(screen.getByRole("button", { name: "下載偏離包" }));
    expect(
      await screen.findByText("呢個瀏覽器儲存唔到檔案，所以冇下載。"),
    ).toBeInTheDocument();

    // The URL was created, so it must also have been revoked exactly once.
    expect(browser.objectUrls).toHaveLength(1);
    expect(browser.revoked).toEqual([browser.objectUrls[0].url]);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(screen.queryByText(/已經下載：/)).not.toBeInTheDocument();
    expect(errors).not.toHaveBeenCalled();
    expect(countCalls("/download")).toBe(1);
    errors.mockRestore();
  });

  it("still clears the anchor and the reference when the revoke itself throws", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn((url: string) => {
        browser.revoked.push(url);
        throw new Error("revoke refused");
      }),
    });

    await user.click(screen.getByRole("button", { name: "下載偏離包" }));
    expect(
      await screen.findByText("呢個瀏覽器儲存唔到檔案，所以冇下載。"),
    ).toBeInTheDocument();

    // Exactly one attempt was made; the page never claims the URL was freed.
    expect(browser.revoked).toHaveLength(1);
    expect(document.querySelector("a[download]")).toBeNull();
    expect(screen.queryByText(/已經下載：/)).not.toBeInTheDocument();
    expect(errors).not.toHaveBeenCalled();

    // A later explicit click still works: no permanent lock was left behind.
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: vi.fn((url: string) => {
        browser.revoked.push(url);
      }),
    });
    await user.click(screen.getByRole("button", { name: "下載偏離包" }));
    await screen.findByText(`已經下載：${READY_FILENAME}`);
    expect(countCalls("/download")).toBe(2);
    expect(browser.revoked).toHaveLength(2);
    errors.mockRestore();
  });
});

/*
 * [238]: the exact error bodies C-v5 captured from the registered FastAPI
 * routes in [237]. Nothing here is a mock preference — each body is the shape
 * the real producer returns, fed into the production consumer.
 */
describe("Phase 4 — HTTP error identity", () => {
  interface ErrorVector {
    code: string;
    status: number;
    retryable?: boolean;
    requestId?: string | null;
    snapshotId?: string | null;
    progress?: {
      completed_parts: number;
      total_parts: number;
      current_part: string | null;
    } | null;
    issues?: unknown[];
  }

  function producerError(vector: ErrorVector): Response {
    return json(
      {
        detail: {
          schema: "paper_review_error.v1",
          code: vector.code,
          message: "backend 原文，唔會出現喺畫面",
          retryable: vector.retryable ?? false,
          request_id: vector.requestId === undefined ? null : vector.requestId,
          snapshot_id:
            vector.snapshotId === undefined ? null : vector.snapshotId,
          progress: vector.progress === undefined ? null : vector.progress,
          issues: vector.issues ?? [],
        },
      },
      vector.status,
    );
  }

  const ACCEPTED_PROGRESS = {
    completed_parts: 3,
    total_parts: 10,
    current_part: null,
  };
  const CONFLICT_PROGRESS = {
    completed_parts: 0,
    total_parts: 10,
    current_part: null,
  };

  async function reachReady(user: ReturnType<typeof userEvent.setup>) {
    const artifact = await ensureArtifact();
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          readyStatusFor((body as { request_id: string }).request_id, artifact),
          201,
        ),
      );
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);
    await screen.findByText("偏離包已經準備好。");
    return artifact;
  }

  it("shows the known reason, progress and issues of an accepted failure", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        producerError({
        code: "artifact_build_failed",
        status: 503,
        requestId: (body as { request_id: string }).request_id,
        snapshotId: SNAPSHOT_ID,
        progress: ACCEPTED_PROGRESS,
        issues: [
          {
            kind: "artifact_build_failed",
            path: "paper/events.json",
            source_ref: null,
            expected_sha256: null,
            actual_sha256: null,
            ref_chain: [],
          },
        ],
        }),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    const dialog = await screen.findByRole("dialog", { name: "偏離包" });
    expect(
      within(dialog).getByText("建立偏離包嗰陣失敗。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("最後已核實：3 ／ 10 部分。"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("建立失敗：paper/events.json"),
    ).toBeInTheDocument();
    // The raw backend sentence never reaches the screen.
    expect(
      screen.queryByText("backend 原文，唔會出現喺畫面"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/結果未知/)).not.toBeInTheDocument();
    expect(countCalls("/review-snapshots", "POST")).toBe(1);
    expect(screen.getByRole("tab", { name: "總覽" })).toBeEnabled();
  });

  it("shows a conflict without ever adopting, rechecking or reposting", async () => {
    harness.reviewCreate = (body) =>
      Promise.resolve(
        producerError({
          code: "request_id_conflict",
          status: 409,
          requestId: (body as { request_id: string }).request_id,
          snapshotId: SNAPSHOT_ID,
          progress: CONFLICT_PROGRESS,
        }),
      );
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);
    await clickReviewCta(user);

    const dialog = await screen.findByRole("dialog", { name: "偏離包" });
    expect(
      within(dialog).getByText(
        "同一個請求對應咗唔同內容，已經停低，唔會建立第二份。",
      ),
    ).toBeInTheDocument();
    // The conflicting snapshot belongs to another intent: it is not adopted.
    expect(screen.queryByText(SNAPSHOT_ID)).not.toBeInTheDocument();
    expect(screen.queryByText("偏離包已經準備好。")).not.toBeInTheDocument();
    expect(screen.queryByText(/準備緊：已完成/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重新檢查狀態" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "下載偏離包" }),
    ).not.toBeInTheDocument();
    expect(countCalls("/api/v1/paper/review-requests/")).toBe(0);
    expect(countCalls("/review-snapshots", "POST")).toBe(1);
    expect(countCalls("/download")).toBe(0);
    expect(countCalls("/terminal-opener")).toBe(0);

    // Only an explicit new snapshot may follow, and it mints a new identity.
    const firstId = postedReviewRequestIds()[0];
    harness.reviewCreate = (body) =>
      Promise.resolve(
        json(
          reviewStatusBody(
            (body as { request_id: string }).request_id,
            "preparing",
          ),
          202,
        ),
      );
    await user.click(screen.getByRole("button", { name: "建立新嘅偏離包" }));
    await screen.findByText("準備緊：已完成 4 ／ 10 部分。");
    const ids = postedReviewRequestIds();
    expect(ids).toHaveLength(2);
    expect(ids[1]).not.toBe(firstId);
    expect(currentSearch).toContain(`review_request=${ids[1]}`);
    expect(countCalls("/api/v1/paper/review-requests/")).toBe(0);
  });

  it("refuses a drifted or mismatched failure body as unknown", async () => {
    const cases: Array<[string, (requestId: string) => Response]> = [
      [
        "snapshot without progress",
        (requestId) =>
          producerError({
            code: "artifact_build_failed",
            status: 503,
            requestId,
            snapshotId: SNAPSHOT_ID,
            progress: null,
          }),
      ],
      [
        "progress without snapshot",
        (requestId) =>
          producerError({
            code: "artifact_build_failed",
            status: 503,
            requestId,
            snapshotId: null,
            progress: ACCEPTED_PROGRESS,
          }),
      ],
      [
        "another request id",
        () =>
          producerError({
            code: "artifact_build_failed",
            status: 503,
            requestId: "00000000-0000-4000-8000-000000000000",
            snapshotId: SNAPSHOT_ID,
            progress: ACCEPTED_PROGRESS,
          }),
      ],
      [
        "status disagrees with the code",
        (requestId) =>
          producerError({
            code: "artifact_build_failed",
            status: 409,
            requestId,
            snapshotId: SNAPSHOT_ID,
            progress: ACCEPTED_PROGRESS,
          }),
      ],
      [
        "retryable disagrees with the code",
        (requestId) =>
          producerError({
            code: "artifact_build_failed",
            status: 503,
            retryable: true,
            requestId,
            snapshotId: SNAPSHOT_ID,
            progress: ACCEPTED_PROGRESS,
          }),
      ],
      [
        "framework 422",
        () =>
          json(
            { detail: [{ loc: ["body", "request_id"], msg: "invalid" }] },
            422,
          ),
      ],
    ];

    for (const [name, responder] of cases) {
      harness.reviewCreate = (body) =>
        Promise.resolve(
          responder((body as { request_id: string }).request_id),
        );
      const user = userEvent.setup();
      const view = renderPage();
      await reachProvisionedTrader(user);
      await clickReviewCta(user);

      expect(
        await screen.findByText(/系統唔會自己重試，亦唔會建立第二份/),
        name,
      ).toBeInTheDocument();
      expect(screen.queryByText("建立偏離包嗰陣失敗。"), name).not.toBeInTheDocument();
      expect(countCalls("/review-snapshots", "POST"), name).toBe(1);
      view.unmount();
      harness.calls.length = 0;
    }
  });

  it("maps a download refusal to its known reason with zero side effects", async () => {
    const user = userEvent.setup();
    await reachReady(user);
    const requestId = postedReviewRequestIds()[0];
    const cases: Array<[string, ErrorVector, string]> = [
      [
        "not ready",
        {
          code: "snapshot_not_ready",
          status: 409,
          retryable: true,
          requestId,
          snapshotId: SNAPSHOT_ID,
          progress: CONFLICT_PROGRESS,
        },
        "偏離包仲準備緊。",
      ],
      [
        "unavailable",
        {
          code: "artifact_unavailable",
          status: 503,
          requestId,
          snapshotId: SNAPSHOT_ID,
          progress: ACCEPTED_PROGRESS,
        },
        "偏離包暫時讀唔到。",
      ],
      [
        "another snapshot",
        {
          code: "snapshot_not_ready",
          status: 409,
          retryable: true,
          requestId,
          snapshotId: "paper-review-00000000000000000000000000000000",
          progress: CONFLICT_PROGRESS,
        },
        "偏離包下載唔到。",
      ],
    ];

    for (const [name, vector, expected] of cases) {
      harness.reviewDownload = () => Promise.resolve(producerError(vector));
      await user.click(screen.getByRole("button", { name: "下載偏離包" }));
      expect(await screen.findByText(expected), name).toBeInTheDocument();
      expect(browser.objectUrls, name).toHaveLength(0);
      expect(browser.anchorClicks, name).toHaveLength(0);
      expect(browser.revoked, name).toHaveLength(0);
      expect(screen.queryByText(/已經下載：/), name).not.toBeInTheDocument();
    }
  });

  it("maps an opener refusal to its known reason with zero clipboard writes", async () => {
    const user = userEvent.setup();
    installClipboardProbe();
    await reachReady(user);
    const requestId = postedReviewRequestIds()[0];
    const cases: Array<[string, ErrorVector, string]> = [
      [
        "not ready",
        {
          code: "snapshot_not_ready",
          status: 409,
          retryable: true,
          requestId,
          snapshotId: SNAPSHOT_ID,
          progress: CONFLICT_PROGRESS,
        },
        "偏離包仲準備緊。",
      ],
      [
        "failed",
        {
          code: "snapshot_failed",
          status: 409,
          requestId,
          snapshotId: SNAPSHOT_ID,
          progress: ACCEPTED_PROGRESS,
        },
        "今次偏離包建立失敗。",
      ],
      [
        "unavailable",
        {
          code: "artifact_unavailable",
          status: 503,
          requestId,
          snapshotId: SNAPSHOT_ID,
          progress: ACCEPTED_PROGRESS,
        },
        "偏離包暫時讀唔到。",
      ],
      [
        "another request",
        {
          code: "snapshot_not_ready",
          status: 409,
          retryable: true,
          requestId: "00000000-0000-4000-8000-000000000000",
          snapshotId: SNAPSHOT_ID,
          progress: CONFLICT_PROGRESS,
        },
        "Terminal 開場白讀唔到。",
      ],
    ];

    for (const [name, vector, expected] of cases) {
      harness.reviewOpener = () => Promise.resolve(producerError(vector));
      await user.click(
        screen.getByRole("button", { name: "複製 Terminal 開場白" }),
      );
      expect(await screen.findByText(expected), name).toBeInTheDocument();
      expect(browser.copied, name).toHaveLength(0);
      expect(
        screen.queryByText("已複製 Terminal 開場白。"),
        name,
      ).not.toBeInTheDocument();
    }
  });
});

/*
 * [241] Option A A3: the provisioning authorization consumer. Authorization and
 * runtime truth are shown apart, the create identity is minted exactly once at
 * confirm, and nothing about the permit is ever sent back to the backend.
 */
describe("A3 — provisioning authorization consumer", () => {
  async function selectAll(user: ReturnType<typeof userEvent.setup>) {
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);
  }

  async function reachPreflight(user: ReturnType<typeof userEvent.setup>) {
    renderPage();
    await selectAll(user);
  }

  function preflightCalls(): Call[] {
    return harness.calls.filter((call) =>
      call.url.endsWith("/api/v1/paper/provisioning-readiness"),
    );
  }

  it("sends exactly the approved preflight body, once, with no retry", async () => {
    const user = userEvent.setup();
    await reachPreflight(user);
    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");

    const calls = preflightCalls();
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/v1/paper/provisioning-readiness");
    const body = calls[0].body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["schema", "selection"]);
    expect(body.schema).toBe("paper_provisioning_readiness_request.v1");
    expect(body.selection).toEqual({
      strategy_id: STRATEGY_ID,
      content_sha256: CONTENT_SHA,
      contract_id: CONTRACT_ID,
      baseline_run_id: RUN_ID,
      baseline_result_sha256: RESULT_SHA,
    });
    // No request id before confirm, and no second attempt of any kind.
    expect(body.request_id).toBeUndefined();
    await settle();
    expect(preflightCalls()).toHaveLength(1);
  });

  it("states the authorization apart from the runtime conditions", async () => {
    harness.provisioning = () =>
      Promise.resolve(
        json(
          provisioningBody({
            statuses: ["unknown", "unknown", "unknown", "ready"],
            marketSession: "unknown",
          }),
        ),
      );
    const user = userEvent.setup();
    await reachPreflight(user);

    expect(
      await screen.findByRole("heading", { name: "建立授權" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "實際啟動條件" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("已授權：可以建立一個未啟用嘅交易員。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /呢個授權只准保存一個未啟用嘅交易員，唔代表可以開始模擬交易、連 IB 或落單。/,
      ),
    ).toBeInTheDocument();
    // The three external checks stay unknown; none of them is painted ready.
    const items = screen.getAllByRole("listitem");
    const unknownRows = items.filter((node) =>
      (node.className || "").includes("paper-check--unknown"),
    );
    expect(unknownRows).toHaveLength(3);
    expect(
      items.filter((node) => (node.className || "").includes("paper-check--ready")),
    ).toHaveLength(1);
    expect(screen.getByText(/而家係咪交易時段暫時確認唔到/)).toBeInTheDocument();
    // No engineering vocabulary reaches the Owner.
    expect(screen.queryByText(/paper_provisioning/)).not.toBeInTheDocument();
    expect(screen.queryByText(/paper-provision-/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Owner-approved one-off P6 provision-only/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/armed/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
  });

  it("refuses to create for every state that is not an armed permit", async () => {
    const cases: Array<[string, string]> = [
      ["disabled", "未授權：而家唔可以建立交易員。"],
      ["claimed", "呢個授權已經用嚟建立一個交易員，唔可以再用。"],
      ["consumed", "呢個授權已經用完，唔可以再用。"],
      ["expired", "授權已經過期，所以而家唔可以建立交易員。"],
    ];
    for (const [state, copy] of cases) {
      harness.provisioning = () =>
        Promise.resolve(json(provisioningBody({ state })));
      const user = userEvent.setup();
      const view = renderPage();
      await selectAll(user);

      expect(await screen.findByText(copy), state).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "建立交易員" }),
        state,
      ).toBeDisabled();
      expect(screen.getByText(/仲欠：建立授權/), state).toBeInTheDocument();
      // Every external check is still ready and still stated as such.
      expect(screen.getByText("已經攞到實時價格。"), state).toBeInTheDocument();
      expect(countCalls("/api/v1/paper/traders", "POST"), state).toBe(0);
      view.unmount();
      harness.calls.length = 0;
    }
  });

  it("keeps the action disabled while the preflight is loading or has failed", async () => {
    const gate = deferred<Response>();
    harness.provisioning = () => gate.promise;
    const user = userEvent.setup();
    await reachPreflight(user);

    expect(await screen.findByText("檢查緊…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();

    gate.resolve(json({ oops: true }));
    expect(
      await screen.findByText(/開始前檢查嘅回覆讀唔到/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(0);
  });

  it("never lets a superseded preflight authorize the new selection", async () => {
    const slow = deferred<Response>();
    let call = 0;
    harness.provisioning = () => {
      call += 1;
      return call === 1
        ? slow.promise
        : Promise.resolve(json(provisioningBody({ state: "expired" })));
    };
    const user = userEvent.setup();
    await reachPreflight(user);

    // A new contract, then a new baseline: the first answer is now stale.
    await user.click(screen.getByRole("button", { name: /E-mini Nasdaq-100/ }));
    await chooseBaseline(user);
    await screen.findByText("授權已經過期，所以而家唔可以建立交易員。");

    slow.resolve(json(provisioningBody()));
    await settle();
    expect(
      screen.getByText("授權已經過期，所以而家唔可以建立交易員。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("已授權：可以建立一個未啟用嘅交易員。"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(0);
  });

  it("mints the create identity only at confirm, exactly once", async () => {
    const user = userEvent.setup();
    const uuid = vi.spyOn(globalThis.crypto, "randomUUID");
    await reachPreflight(user);
    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");

    // Preflight and the confirmation sheet mint nothing.
    expect(uuid).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await screen.findByRole("button", { name: "確認並建立交易員" });
    expect(uuid).not.toHaveBeenCalled();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(0);

    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [traderBody(createdRequestIds()[0] ?? "")],
        }),
      );
    await user.click(screen.getByRole("button", { name: "確認並建立交易員" }));
    await screen.findByRole("region", { name: "交易員詳情" });

    expect(uuid).toHaveBeenCalledTimes(1);
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(new Set(createdRequestIds()).size).toBe(1);
    uuid.mockRestore();
  });

  it("sends a create body that carries nothing about the permit", async () => {
    const user = userEvent.setup();
    await reachPreflight(user);
    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );
    await waitFor(() => {
      expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    });

    const call = harness.calls.find(
      (row) => row.method === "POST" && row.url.endsWith("/api/v1/paper/traders"),
    );
    const body = call?.body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      "request_id",
      "schema",
      "selection",
    ]);
    expect(body.schema).toBe("paper_trader_create_request.v1");
    expect(Object.keys(body.selection as Record<string, unknown>).sort()).toEqual([
      "baseline_result_sha256",
      "baseline_run_id",
      "content_sha256",
      "contract_id",
      "strategy_id",
    ]);
    // Nothing about the permit, the runtime or the audit reason travels back.
    const serialised = JSON.stringify(body);
    expect(serialised).not.toContain("paper-provision-");
    expect(serialised).not.toContain("operation");
    expect(serialised).not.toContain("armed");
    expect(serialised).not.toContain("authorization");
    expect(serialised).not.toContain("runtime");
    expect(serialised).not.toContain(PERMIT_REASON);
  });

  it("keeps the success and detail wording honest about the engine", async () => {
    const user = userEvent.setup();
    renderPage();
    await reachProvisionedTrader(user);

    expect(
      screen.getByText(/已建立 · 新分頁已經加咗喺上面/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/已建立；模擬引擎尚未啟用/).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("模擬引擎尚未啟用。")).toBeInTheDocument();
    expect(screen.queryByText(/已經開始/)).not.toBeInTheDocument();
    expect(screen.queryByText(/運行中/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已連接實時價格/)).not.toBeInTheDocument();
  });

  it("survives the production StrictMode boundary with one preflight", async () => {
    currentSearch = "";
    const user = userEvent.setup();
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/paper"]}>
          <PaperPage />
          <LocationProbe />
        </MemoryRouter>
      </StrictMode>,
    );
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);

    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");
    expect(preflightCalls()).toHaveLength(1);
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
  });

  it("issues one preflight per explicit recheck and never auto polls", async () => {
    const user = userEvent.setup();
    await reachPreflight(user);
    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");
    expect(preflightCalls()).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "重新檢查" }));
    await waitFor(() => {
      expect(preflightCalls()).toHaveLength(2);
    });
    await settle();
    expect(preflightCalls()).toHaveLength(2);
  });
});

/*
 * [243] Correction A + [243-A]: the page must never say "so you cannot create"
 * about an external provider while it also says the create is authorized, it
 * must read the four Option A refusals as plain sentences, and it must adopt
 * the real A2 record whose persisted snapshot is blocked.
 */
describe("Correction A — authorization copy, Option A errors and blocked records", () => {
  async function selectAll(user: ReturnType<typeof userEvent.setup>) {
    await openNewTraderTab(user);
    await chooseStrategy(user);
    await chooseContract(user);
    await chooseBaseline(user);
  }

  const OPTION_A_FAILURES: Array<[string, number, string]> = [
    [
      "external_readiness_invalid",
      503,
      "實際啟動條件嘅回覆讀唔到，所以而家唔可以建立交易員。",
    ],
    [
      "provisioning_not_authorized",
      503,
      "而家未有建立授權，所以唔可以建立交易員。",
    ],
    [
      "provisioning_selection_mismatch",
      409,
      "你揀嘅內容唔喺今次建立授權範圍之內，所以唔可以建立交易員。",
    ],
    [
      "provisioning_request_conflict",
      409,
      "今次建立授權已經俾另一個請求用咗，所以唔會建立第二個交易員。",
    ],
  ];

  it("never contradicts itself while an authorized create is offered", async () => {
    harness.provisioning = () =>
      Promise.resolve(
        json(
          provisioningBody({
            statuses: ["blocked", "unknown", "unknown", "ready"],
            marketSession: "unknown",
          }),
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await selectAll(user);

    expect(
      await screen.findByText("已授權：可以建立一個未啟用嘅交易員。"),
    ).toBeInTheDocument();
    // The external providers state the runtime consequence, never a refusal.
    expect(
      screen.getByText("而家攞唔到實時價格，所以建立之後引擎唔會開始。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("確認唔到交易日資料，所以建立之後引擎唔會開始。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("確認唔到通知係咪通得到，所以建立之後引擎唔會開始。"),
    ).toBeInTheDocument();
    // Not one sentence on the whole screen claims creation is refused.
    expect(screen.queryByText(/所以唔可以建立/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeEnabled();
  });

  it("still refuses to create when the locked baseline itself is not verified", async () => {
    harness.provisioning = () =>
      Promise.resolve(
        json(
          provisioningBody({
            statuses: ["ready", "ready", "ready", "unknown"],
            canProvision: false,
          }),
        ),
      );
    const user = userEvent.setup();
    renderPage();
    await selectAll(user);

    expect(
      await screen.findByText(
        "確認唔到你揀嗰次回測嘅記錄，所以唔可以建立。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();
    expect(screen.getByText(/仲欠：建立授權/)).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(0);
  });

  it("reads every Option A create refusal as a plain sentence and stops there", async () => {
    for (const [code, status, copy] of OPTION_A_FAILURES) {
      harness.create = () => Promise.resolve(apiError(code, status));
      const uuid = vi.spyOn(globalThis.crypto, "randomUUID");
      const user = userEvent.setup();
      const view = renderPage();
      await selectAll(user);
      await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");
      await user.click(screen.getByRole("button", { name: "建立交易員" }));
      await user.click(
        await screen.findByRole("button", { name: "確認並建立交易員" }),
      );

      expect(await screen.findByText(copy), code).toBeInTheDocument();
      // Exactly one POST, one identity, and no recovery lookup at all.
      expect(countCalls("/api/v1/paper/traders", "POST"), code).toBe(1);
      expect(uuid, code).toHaveBeenCalledTimes(1);
      expect(countCalls("/api/v1/paper/trader-requests/"), code).toBe(0);
      // No raw engineering vocabulary reaches the Owner.
      expect(screen.queryByText(new RegExp(code)), code).not.toBeInTheDocument();
      expect(screen.queryByText(/HTTP /), code).not.toBeInTheDocument();
      expect(screen.queryByText(/paper_api_error/), code).not.toBeInTheDocument();
      expect(screen.queryByText(/人話原因/), code).not.toBeInTheDocument();
      // The selection is kept and the Owner may act again explicitly.
      expect(screen.getByText("USD 100,000"), code).toBeInTheDocument();

      uuid.mockRestore();
      view.unmount();
      harness.calls.length = 0;
    }
  });

  it("shows a plain sentence for a refused preflight, never a raw status", async () => {
    harness.provisioning = () =>
      Promise.resolve(apiError("provisioning_not_authorized", 503));
    const user = userEvent.setup();
    renderPage();
    await selectAll(user);

    expect(
      await screen.findByText("而家未有建立授權，所以唔可以建立交易員。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/HTTP /)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "建立交易員" })).toBeDisabled();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(0);
  });

  it("adopts the real A2 record whose persisted snapshot is blocked", async () => {
    const blockedTrader = (requestId: string) => {
      const body = traderBody(requestId);
      body.readiness_snapshot.checks = checkRows([
        "unknown",
        "unknown",
        "blocked",
        "ready",
      ]);
      body.readiness_snapshot.overall = "blocked";
      return body;
    };
    harness.provisioning = () =>
      Promise.resolve(
        json(
          provisioningBody({ statuses: ["unknown", "unknown", "blocked", "ready"] }),
        ),
      );
    harness.create = (body) =>
      Promise.resolve(
        json(blockedTrader((body as { request_id: string }).request_id), 201),
      );
    harness.detail = () =>
      Promise.resolve(json(blockedTrader(createdRequestIds()[0] ?? "")));

    const user = userEvent.setup();
    renderPage();
    await selectAll(user);
    await screen.findByText("已授權：可以建立一個未啟用嘅交易員。");
    harness.list = () =>
      Promise.resolve(
        json({
          schema: "paper_trader_list.v1",
          count: 1,
          traders: [blockedTrader(createdRequestIds()[0])],
        }),
      );
    await user.click(screen.getByRole("button", { name: "建立交易員" }));
    await user.click(
      await screen.findByRole("button", { name: "確認並建立交易員" }),
    );

    // The record is adopted: no unknown recovery, no second POST, no lookup.
    expect(
      await screen.findByRole("region", { name: "交易員詳情" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/已建立 · 新分頁已經加咗喺上面/)).toBeInTheDocument();
    expect(countCalls("/api/v1/paper/traders", "POST")).toBe(1);
    expect(countCalls("/api/v1/paper/trader-requests/")).toBe(0);
    expect(screen.queryByText(/建立結果未知/)).not.toBeInTheDocument();
    // And it still only ever claims the engine is off.
    expect(
      screen.getAllByText(/已建立；模擬引擎尚未啟用/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/已經開始/)).not.toBeInTheDocument();
    expect(screen.queryByText(/運行中/)).not.toBeInTheDocument();
  });
});
