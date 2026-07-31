import type {
  PaperReviewReady,
  PaperReviewTerminalOpenerClaim,
} from "./types";

/*
 * P6 Stage B download and opener verification (design §9.3, §9.4, §11.1,
 * §11.4; work order [229] §7 and §8).
 *
 * Everything here is pure: no fetch, no DOM, no clipboard, no Blob and no
 * storage. It reads bytes the caller already holds and answers one question —
 * may these bytes be handed to the Owner? A `false` answer is always fail
 * closed: the caller must not build a Blob, an object URL or a clipboard write.
 *
 * The ZIP reader deliberately does not use a ZIP library. A library keyed by
 * filename cannot prove entry order and silently collapses a duplicated
 * filename, which is exactly one of the frauds this batch must catch.
 */

const SNAPSHOT_ID = /^paper-review-[0-9a-f]{32}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;

/** §3.1: the snapshot identity that reaches a URL path segment. */
export function isPaperReviewSnapshotId(value: unknown): value is string {
  return typeof value === "string" && SNAPSHOT_ID.test(value);
}

export type PaperVerifyResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: string; readonly detail: string };

function fail(error: string, detail: string): { ok: false; error: string; detail: string } {
  return { ok: false, error, detail };
}

/** Owner-facing reasons; the exact technical fact travels in `detail`. */
const INTEGRITY = "下載到嘅偏離包內容核對唔通過，所以冇儲存落嚟。";
const TRANSPORT = "偏離包下載唔到。";
const OPENER_INTEGRITY = "Terminal 開場白核對唔通過，所以冇複製。";

export interface PaperZipEntry {
  readonly path: string;
  /** Declared uncompressed size; STORE means it is also the stored size. */
  readonly bytes: number;
  readonly localHeaderOffset: number;
  readonly dataStart: number;
}

const SIGNATURE_EOCD = 0x06054b50;
const SIGNATURE_EOCD64_LOCATOR = 0x07064b50;
const SIGNATURE_CENTRAL = 0x02014b50;
const SIGNATURE_LOCAL = 0x04034b50;
const MAX_COMMENT = 0xffff;
const EOCD_SIZE = 22;
const CENTRAL_FIXED = 46;
const LOCAL_FIXED = 30;

/**
 * A ZIP path is only acceptable when it is a plain relative POSIX path. Any
 * backslash, drive letter, absolute path, dot segment, empty segment, trailing
 * separator or control character is refused rather than normalised.
 */
function isSafeMemberPath(path: string): boolean {
  if (path.length === 0 || path.length > 512) {
    return false;
  }
  if (path.includes("\\") || path.startsWith("/") || path.endsWith("/")) {
    return false;
  }
  if (/^[A-Za-z]:/.test(path)) {
    return false;
  }
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(path)) {
    return false;
  }
  return path
    .split("/")
    .every((segment) => segment.length > 0 && segment !== "." && segment !== "..");
}

function decodeUtf8Strict(bytes: Uint8Array): string | null {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

function findEocd(view: DataView, total: number): number | null {
  const earliest = Math.max(0, total - EOCD_SIZE - MAX_COMMENT);
  for (let offset = total - EOCD_SIZE; offset >= earliest; offset -= 1) {
    if (view.getUint32(offset, true) === SIGNATURE_EOCD) {
      return offset;
    }
  }
  return null;
}

/**
 * Reads the central directory with a bounded `DataView`. Order, count,
 * duplicates and declared sizes all come from the raw records, never from a
 * filename-keyed map.
 */
export function readPaperZipDirectory(
  bytes: Uint8Array,
): PaperVerifyResult<readonly PaperZipEntry[]> {
  const total = bytes.byteLength;
  if (total < EOCD_SIZE) {
    return fail(INTEGRITY, `檔案只有 ${String(total)} bytes，短過一個 ZIP 結尾記錄`);
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, total);
  const eocd = findEocd(view, total);
  if (eocd === null) {
    return fail(INTEGRITY, "搵唔到 ZIP 結尾記錄（EOCD）");
  }
  const diskNumber = view.getUint16(eocd + 4, true);
  const centralDisk = view.getUint16(eocd + 6, true);
  const entriesThisDisk = view.getUint16(eocd + 8, true);
  const entriesTotal = view.getUint16(eocd + 10, true);
  const centralSize = view.getUint32(eocd + 12, true);
  const centralOffset = view.getUint32(eocd + 16, true);
  const commentLength = view.getUint16(eocd + 20, true);
  if (diskNumber !== 0 || centralDisk !== 0) {
    return fail(INTEGRITY, "ZIP 分咗多過一個磁碟片段");
  }
  if (entriesThisDisk !== entriesTotal) {
    return fail(INTEGRITY, "ZIP 結尾記錄嘅項目數自相矛盾");
  }
  if (eocd + EOCD_SIZE + commentLength !== total) {
    return fail(INTEGRITY, "ZIP 結尾記錄之後仲有多餘 bytes");
  }
  // §11.4: the approved producer writes no archive comment at all.
  if (commentLength !== 0) {
    return fail(INTEGRITY, "ZIP 帶咗結尾註解");
  }
  if (
    entriesTotal === 0xffff ||
    centralSize === 0xffffffff ||
    centralOffset === 0xffffffff
  ) {
    return fail(INTEGRITY, "ZIP 用咗 ZIP64 擴充，唔喺批准嘅格式入面");
  }
  if (
    eocd >= 20 &&
    view.getUint32(eocd - 20, true) === SIGNATURE_EOCD64_LOCATOR
  ) {
    return fail(INTEGRITY, "ZIP 帶住 ZIP64 定位記錄");
  }
  if (centralOffset + centralSize !== eocd) {
    return fail(INTEGRITY, "ZIP 中央目錄位置同大小對唔上結尾記錄");
  }

  const entries: PaperZipEntry[] = [];
  let cursor = centralOffset;
  for (let index = 0; index < entriesTotal; index += 1) {
    if (cursor + CENTRAL_FIXED > eocd) {
      return fail(INTEGRITY, `第 ${String(index + 1)} 個中央目錄記錄超出範圍`);
    }
    if (view.getUint32(cursor, true) !== SIGNATURE_CENTRAL) {
      return fail(INTEGRITY, `第 ${String(index + 1)} 個中央目錄記錄簽名唔啱`);
    }
    const flags = view.getUint16(cursor + 8, true);
    const method = view.getUint16(cursor + 10, true);
    const compressedSize = view.getUint32(cursor + 20, true);
    const uncompressedSize = view.getUint32(cursor + 24, true);
    const nameLength = view.getUint16(cursor + 28, true);
    const extraLength = view.getUint16(cursor + 30, true);
    const commentLen = view.getUint16(cursor + 32, true);
    const startDisk = view.getUint16(cursor + 34, true);
    const localOffset = view.getUint32(cursor + 42, true);
    const label = `第 ${String(index + 1)} 個成員`;
    if ((flags & 0x0001) !== 0 || (flags & 0x0040) !== 0) {
      return fail(INTEGRITY, `${label}標示咗加密`);
    }
    if ((flags & 0x0008) !== 0) {
      return fail(INTEGRITY, `${label}用咗延後大小記錄，大小無法確定`);
    }
    if (method !== 0) {
      return fail(INTEGRITY, `${label}唔係未壓縮存放（方法 ${String(method)}）`);
    }
    if (compressedSize !== uncompressedSize) {
      return fail(INTEGRITY, `${label}宣告嘅壓縮前後大小唔一致`);
    }
    if (extraLength !== 0 || commentLen !== 0) {
      return fail(INTEGRITY, `${label}帶咗額外欄位或註解`);
    }
    if (startDisk !== 0) {
      return fail(INTEGRITY, `${label}唔喺第一個磁碟片段`);
    }
    const nameStart = cursor + CENTRAL_FIXED;
    if (nameStart + nameLength > eocd) {
      return fail(INTEGRITY, `${label}嘅名超出中央目錄範圍`);
    }
    const path = decodeUtf8Strict(bytes.subarray(nameStart, nameStart + nameLength));
    if (path === null) {
      return fail(INTEGRITY, `${label}嘅名唔係合法 UTF-8`);
    }
    if (!isSafeMemberPath(path)) {
      return fail(INTEGRITY, `${label}嘅路徑唔安全：${path}`);
    }

    // The local header is read too: a central record alone cannot prove where
    // the bytes are, and a mismatch between the two is itself a forgery.
    if (localOffset + LOCAL_FIXED > centralOffset) {
      return fail(INTEGRITY, `${label}嘅本地記錄超出範圍`);
    }
    if (view.getUint32(localOffset, true) !== SIGNATURE_LOCAL) {
      return fail(INTEGRITY, `${label}嘅本地記錄簽名唔啱`);
    }
    const localFlags = view.getUint16(localOffset + 6, true);
    const localMethod = view.getUint16(localOffset + 8, true);
    const localCompressed = view.getUint32(localOffset + 18, true);
    const localUncompressed = view.getUint32(localOffset + 22, true);
    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    if ((localFlags & 0x0009) !== 0 || (localFlags & 0x0040) !== 0) {
      return fail(INTEGRITY, `${label}嘅本地記錄標示咗加密或延後大小`);
    }
    if (localMethod !== 0) {
      return fail(INTEGRITY, `${label}嘅本地記錄唔係未壓縮存放`);
    }
    if (
      localCompressed !== compressedSize ||
      localUncompressed !== uncompressedSize
    ) {
      return fail(INTEGRITY, `${label}嘅本地記錄同中央目錄大小唔一致`);
    }
    if (localNameLength !== nameLength) {
      return fail(INTEGRITY, `${label}嘅本地記錄名長度唔一致`);
    }
    // §11.4: the approved producer writes no extra field on either header.
    if (localExtraLength !== 0) {
      return fail(INTEGRITY, `${label}嘅本地記錄帶咗額外欄位`);
    }
    const localNameStart = localOffset + LOCAL_FIXED;
    const localName = decodeUtf8Strict(
      bytes.subarray(localNameStart, localNameStart + localNameLength),
    );
    if (localName !== path) {
      return fail(INTEGRITY, `${label}嘅本地記錄名同中央目錄唔一致`);
    }
    const dataStart = localNameStart + localNameLength + localExtraLength;
    if (dataStart + uncompressedSize > centralOffset) {
      return fail(INTEGRITY, `${label}嘅內容超出中央目錄之前嘅範圍`);
    }
    entries.push({
      path,
      bytes: uncompressedSize,
      localHeaderOffset: localOffset,
      dataStart,
    });
    cursor = nameStart + nameLength + extraLength + commentLen;
  }
  if (cursor !== eocd) {
    return fail(INTEGRITY, "中央目錄長度同記錄總和對唔上");
  }
  return { ok: true, value: entries };
}

/**
 * Browser Web Crypto only. When it is unavailable the answer is `null`, which
 * every caller treats as "not verified" — never as a pass.
 */
export async function paperSha256Hex(
  bytes: Uint8Array,
): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle as SubtleCrypto | undefined;
  if (typeof subtle?.digest !== "function") {
    return null;
  }
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  const digest = await subtle.digest("SHA-256", copy);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export interface PaperReviewDownloadInput {
  readonly status: number;
  readonly contentType: string | null;
  readonly contentDisposition: string | null;
  readonly bytes: Uint8Array;
  readonly ready: PaperReviewReady;
  readonly expectedPaths: readonly string[];
}

export interface PaperReviewVerifiedArtifact {
  readonly bytes: Uint8Array;
  readonly filename: string;
  readonly sha256: string;
  readonly members: readonly PaperZipEntry[];
}

/**
 * §7: the exact ordered pipeline. The first failure stops everything, so a
 * caller can only reach a Blob after all ten checks have passed.
 */
export async function verifyPaperReviewArtifact(
  input: PaperReviewDownloadInput,
): Promise<PaperVerifyResult<PaperReviewVerifiedArtifact>> {
  const { ready } = input;
  if (input.status !== 200) {
    return fail(TRANSPORT, `HTTP ${String(input.status)}`);
  }
  if (input.contentType !== "application/zip") {
    return fail(TRANSPORT, `Content-Type 係 ${String(input.contentType)}`);
  }
  const expectedDisposition = `attachment; filename="${ready.display_filename}"`;
  if (input.contentDisposition !== expectedDisposition) {
    return fail(
      TRANSPORT,
      `Content-Disposition 係 ${String(input.contentDisposition)}`,
    );
  }
  if (input.bytes.byteLength !== ready.artifact_bytes) {
    return fail(
      INTEGRITY,
      `整包大小 ${String(input.bytes.byteLength)} bytes，記錄係 ${String(
        ready.artifact_bytes,
      )} bytes`,
    );
  }
  const whole = await paperSha256Hex(input.bytes);
  if (whole === null) {
    return fail(INTEGRITY, "呢個瀏覽器計唔到雜湊，所以唔敢當作核對過");
  }
  if (whole !== ready.artifact_sha256) {
    return fail(INTEGRITY, `整包雜湊係 ${whole}，記錄係 ${ready.artifact_sha256}`);
  }

  const directory = readPaperZipDirectory(input.bytes);
  if (!directory.ok) {
    return directory;
  }
  const entries = directory.value;
  const expected = input.expectedPaths;
  if (expected.length !== ready.members.length) {
    return fail(INTEGRITY, "要求嘅成員清單同記錄長度唔一致");
  }
  if (entries.length !== expected.length) {
    return fail(
      INTEGRITY,
      `ZIP 有 ${String(entries.length)} 個成員，記錄係 ${String(expected.length)} 個`,
    );
  }
  const seen = new Set<string>();
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const member = ready.members[index];
    const position = String(index + 1);
    if (entry.path !== expected[index] || member.path !== expected[index]) {
      return fail(
        INTEGRITY,
        `第 ${position} 個成員係 ${entry.path}，應該係 ${expected[index]}`,
      );
    }
    if (seen.has(entry.path)) {
      return fail(INTEGRITY, `成員 ${entry.path} 出現多過一次`);
    }
    seen.add(entry.path);
    if (entry.bytes !== member.bytes) {
      return fail(
        INTEGRITY,
        `${entry.path} 宣告 ${String(entry.bytes)} bytes，記錄係 ${String(member.bytes)} bytes`,
      );
    }
  }

  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    const member = ready.members[index];
    const actual = input.bytes.subarray(entry.dataStart, entry.dataStart + entry.bytes);
    if (actual.byteLength !== member.bytes) {
      return fail(INTEGRITY, `${entry.path} 實際內容長度同記錄唔一致`);
    }
    const digest = await paperSha256Hex(actual);
    if (digest === null) {
      return fail(INTEGRITY, "呢個瀏覽器計唔到雜湊，所以唔敢當作核對過");
    }
    if (digest !== member.sha256) {
      return fail(
        INTEGRITY,
        `${entry.path} 內容雜湊係 ${digest}，記錄係 ${member.sha256}`,
      );
    }
  }

  return {
    ok: true,
    value: {
      bytes: input.bytes,
      filename: ready.display_filename,
      sha256: whole,
      members: entries,
    },
  };
}

export interface PaperReviewOpenerInput {
  readonly claim: PaperReviewTerminalOpenerClaim;
  readonly ready: PaperReviewReady;
  readonly snapshotId: string;
}

/**
 * §8: the persisted text is the only truth. Nothing here builds, joins,
 * translates or completes any part of it.
 */
export async function verifyPaperReviewOpener(
  input: PaperReviewOpenerInput,
): Promise<PaperVerifyResult<{ readonly text: string }>> {
  const { claim, ready } = input;
  if (!isPaperReviewSnapshotId(input.snapshotId)) {
    return fail(OPENER_INTEGRITY, "而家嘅偏離包身份唔安全");
  }
  if (claim.snapshot_id !== input.snapshotId) {
    return fail(
      OPENER_INTEGRITY,
      `開場白屬於 ${claim.snapshot_id}，而家嘅偏離包係 ${input.snapshotId}`,
    );
  }
  if (!SHA256_HEX.test(claim.sha256)) {
    return fail(OPENER_INTEGRITY, "開場白雜湊格式唔啱");
  }
  const encoded = new TextEncoder().encode(claim.text);
  if (encoded.byteLength !== claim.bytes) {
    return fail(
      OPENER_INTEGRITY,
      `開場白實際 ${String(encoded.byteLength)} bytes，回應話 ${String(claim.bytes)} bytes`,
    );
  }
  const digest = await paperSha256Hex(encoded);
  if (digest === null) {
    return fail(OPENER_INTEGRITY, "呢個瀏覽器計唔到雜湊，所以唔敢當作核對過");
  }
  if (digest !== claim.sha256) {
    return fail(
      OPENER_INTEGRITY,
      `開場白雜湊係 ${digest}，回應話 ${claim.sha256}`,
    );
  }
  if (claim.bytes !== ready.terminal_opener.bytes) {
    return fail(
      OPENER_INTEGRITY,
      `開場白 ${String(claim.bytes)} bytes，偏離包記錄係 ${String(
        ready.terminal_opener.bytes,
      )} bytes`,
    );
  }
  if (claim.sha256 !== ready.terminal_opener.sha256) {
    return fail(
      OPENER_INTEGRITY,
      `開場白雜湊同偏離包記錄唔一致（${claim.sha256} ／ ${ready.terminal_opener.sha256}）`,
    );
  }
  return { ok: true, value: { text: claim.text } };
}
