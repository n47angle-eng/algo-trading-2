import JSZip from "jszip";
import { describe, expect, it } from "vitest";

import {
  isPaperReviewSnapshotId,
  paperSha256Hex,
  readPaperZipDirectory,
  verifyPaperReviewArtifact,
  verifyPaperReviewOpener,
} from "./reviewDownload";
import { paperReviewMemberPaths } from "./reviewContract";
import type {
  PaperReviewReady,
  PaperReviewTerminalOpenerClaim,
} from "./types";

/*
 * Every ZIP here is a test-owned in-memory fixture: no server, no browser, no
 * real download and no file on disk. The builder writes raw records so a
 * malformed archive can be produced deliberately — a ZIP library would quietly
 * repair exactly the frauds this suite has to catch.
 */

const TRADER_ID = "trader-1f2e3d4c5b6a798877665544332211ff";
const SNAPSHOT_ID = "paper-review-fedcba9876543210fedcba9876543210";
const RUN_ID = "nq-20260728-standard-365adf";
const CAPTURED_AT = "2026-07-29T00:00:00Z";
const FILENAME = `paper-review-${TRADER_ID}-20260729T000000000000Z.zip`;
const PATHS = paperReviewMemberPaths(RUN_ID) ?? [];

interface FixtureEntry {
  path: string;
  content: Uint8Array;
}

interface BuildOptions {
  /** 0 = STORE; anything else must be refused before extraction. */
  method?: number;
  flags?: number;
  centralExtra?: number;
  centralComment?: number;
  /** Bytes of local-header extra field; the approved profile has none. */
  localExtra?: number;
  /** Declared size override per entry index. */
  declared?: (index: number, actual: number) => number;
  zip64Locator?: boolean;
  entriesTotalOverride?: number;
  trailingBytes?: number;
}

function textBytes(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function memberContent(path: string): Uint8Array {
  return textBytes(`{"member":"${path}","stage":"b"}\n`);
}

function canonicalEntries(paths: readonly string[] = PATHS): FixtureEntry[] {
  return paths.map((path) => ({ path, content: memberContent(path) }));
}

/** Minimal raw ZIP writer: local headers, then central directory, then EOCD. */
function buildZip(
  entries: readonly FixtureEntry[],
  options: BuildOptions = {},
): Uint8Array {
  const method = options.method ?? 0;
  const flags = options.flags ?? 0;
  const centralExtra = options.centralExtra ?? 0;
  const centralComment = options.centralComment ?? 0;
  const localExtra = options.localExtra ?? 0;
  const names = entries.map((entry) => textBytes(entry.path));
  const declared = entries.map((entry, index) =>
    options.declared
      ? options.declared(index, entry.content.byteLength)
      : entry.content.byteLength,
  );

  let localSize = 0;
  for (let index = 0; index < entries.length; index += 1) {
    localSize +=
      30 + names[index].byteLength + localExtra + entries[index].content.byteLength;
  }
  let centralSize = 0;
  for (let index = 0; index < entries.length; index += 1) {
    centralSize += 46 + names[index].byteLength + centralExtra + centralComment;
  }
  const locatorSize = options.zip64Locator === true ? 20 : 0;
  const trailing = options.trailingBytes ?? 0;
  const total = localSize + centralSize + locatorSize + 22 + trailing;
  const bytes = new Uint8Array(total);
  const view = new DataView(bytes.buffer);

  const offsets: number[] = [];
  let cursor = 0;
  for (let index = 0; index < entries.length; index += 1) {
    offsets.push(cursor);
    view.setUint32(cursor, 0x04034b50, true);
    view.setUint16(cursor + 4, 20, true);
    view.setUint16(cursor + 6, flags, true);
    view.setUint16(cursor + 8, method, true);
    view.setUint16(cursor + 10, 0, true); // 1980-01-01 00:00:00
    view.setUint16(cursor + 12, 33, true);
    view.setUint32(cursor + 14, 0, true);
    view.setUint32(cursor + 18, declared[index], true);
    view.setUint32(cursor + 22, declared[index], true);
    view.setUint16(cursor + 26, names[index].byteLength, true);
    view.setUint16(cursor + 28, localExtra, true);
    bytes.set(names[index], cursor + 30);
    for (let pad = 0; pad < localExtra; pad += 1) {
      // A plausible extra field, not zero padding: 0x5455 is a real tag.
      bytes[cursor + 30 + names[index].byteLength + pad] = pad === 0 ? 0x55 : 0x54;
    }
    bytes.set(
      entries[index].content,
      cursor + 30 + names[index].byteLength + localExtra,
    );
    cursor +=
      30 + names[index].byteLength + localExtra + entries[index].content.byteLength;
  }

  const centralOffset = cursor;
  for (let index = 0; index < entries.length; index += 1) {
    view.setUint32(cursor, 0x02014b50, true);
    view.setUint16(cursor + 4, 20, true);
    view.setUint16(cursor + 6, 20, true);
    view.setUint16(cursor + 8, flags, true);
    view.setUint16(cursor + 10, method, true);
    view.setUint16(cursor + 12, 0, true);
    view.setUint16(cursor + 14, 33, true);
    view.setUint32(cursor + 16, 0, true);
    view.setUint32(cursor + 20, declared[index], true);
    view.setUint32(cursor + 24, declared[index], true);
    view.setUint16(cursor + 28, names[index].byteLength, true);
    view.setUint16(cursor + 30, centralExtra, true);
    view.setUint16(cursor + 32, centralComment, true);
    view.setUint16(cursor + 34, 0, true);
    view.setUint16(cursor + 36, 0, true);
    view.setUint32(cursor + 38, 0o600 << 16, true);
    view.setUint32(cursor + 42, offsets[index], true);
    bytes.set(names[index], cursor + 46);
    cursor += 46 + names[index].byteLength + centralExtra + centralComment;
  }

  if (options.zip64Locator === true) {
    view.setUint32(cursor, 0x07064b50, true);
    cursor += 20;
  }

  view.setUint32(cursor, 0x06054b50, true);
  view.setUint16(cursor + 4, 0, true);
  view.setUint16(cursor + 6, 0, true);
  const declaredEntries = options.entriesTotalOverride ?? entries.length;
  view.setUint16(cursor + 8, declaredEntries, true);
  view.setUint16(cursor + 10, declaredEntries, true);
  view.setUint32(cursor + 12, centralSize, true);
  view.setUint32(cursor + 16, centralOffset, true);
  view.setUint16(cursor + 20, trailing, true);
  return bytes;
}

async function readyFor(
  bytes: Uint8Array,
  entries: readonly FixtureEntry[],
): Promise<PaperReviewReady> {
  const members = [];
  for (const entry of entries) {
    members.push({
      path: entry.path,
      bytes: entry.content.byteLength,
      sha256: (await paperSha256Hex(entry.content)) ?? "",
    });
  }
  const openerText = "persisted opener text";
  return {
    schema: "paper_review_ready.v1",
    display_filename: FILENAME,
    artifact_bytes: bytes.byteLength,
    artifact_sha256: (await paperSha256Hex(bytes)) ?? "",
    member_count: 10,
    members,
    terminal_opener: {
      bytes: textBytes(openerText).byteLength,
      sha256: (await paperSha256Hex(textBytes(openerText))) ?? "",
    },
    ready_at: CAPTURED_AT,
  };
}

async function canonicalArtifact(): Promise<{
  bytes: Uint8Array;
  ready: PaperReviewReady;
}> {
  const entries = canonicalEntries();
  const bytes = buildZip(entries);
  return { bytes, ready: await readyFor(bytes, entries) };
}

function downloadInput(
  bytes: Uint8Array,
  ready: PaperReviewReady,
  overrides: Partial<{
    status: number;
    contentType: string | null;
    contentDisposition: string | null;
  }> = {},
) {
  return {
    status: overrides.status ?? 200,
    contentType:
      overrides.contentType === undefined
        ? "application/zip"
        : overrides.contentType,
    contentDisposition:
      overrides.contentDisposition === undefined
        ? `attachment; filename="${ready.display_filename}"`
        : overrides.contentDisposition,
    bytes,
    ready,
    expectedPaths: PATHS,
  };
}

describe("snapshot identity", () => {
  it("accepts only the canonical snapshot id", () => {
    expect(isPaperReviewSnapshotId(SNAPSHOT_ID)).toBe(true);
    for (const bad of [
      SNAPSHOT_ID.toUpperCase(),
      "paper-review-fedcba",
      "paper-review-../escape",
      "paper-review-fedcba9876543210fedcba9876543210 ",
      "",
      null,
      42,
    ]) {
      expect(isPaperReviewSnapshotId(bad)).toBe(false);
    }
  });
});

describe("Web Crypto digest", () => {
  it("is a real SHA-256, checked against a known vector", async () => {
    expect(await paperSha256Hex(new Uint8Array())).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
  });
});

describe("raw ZIP directory", () => {
  it("reports the ten members in stored order with their declared sizes", () => {
    const entries = canonicalEntries();
    const result = readPaperZipDirectory(buildZip(entries));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.map((entry) => entry.path)).toEqual([...PATHS]);
      expect(result.value.map((entry) => entry.bytes)).toEqual(
        entries.map((entry) => entry.content.byteLength),
      );
    }
  });

  it("agrees with an independent ZIP library on paths and content", async () => {
    const entries = canonicalEntries();
    const bytes = buildZip(entries);
    const zip = await JSZip.loadAsync(bytes);
    const libraryPaths = Object.keys(zip.files);
    expect(libraryPaths.sort()).toEqual([...PATHS].sort());
    for (const entry of entries) {
      const file = zip.file(entry.path);
      expect(file).not.toBeNull();
      const content = await file!.async("uint8array");
      expect(Array.from(content)).toEqual(Array.from(entry.content));
    }
  });

  it("refuses a duplicated filename that a library would collapse", async () => {
    const entries = canonicalEntries();
    const duplicated = [...entries, { ...entries[0] }];
    const bytes = buildZip(duplicated);
    // The library keeps ten keys; the raw reader still sees eleven records.
    const zip = await JSZip.loadAsync(bytes);
    expect(Object.keys(zip.files)).toHaveLength(10);
    const result = readPaperZipDirectory(bytes);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value).toHaveLength(11);
    }
  });

  it("fails closed on malformed, multi-part and ZIP64 archives", () => {
    const entries = canonicalEntries();
    const good = buildZip(entries);
    const cases: Array<[string, Uint8Array]> = [
      ["truncated tail", good.subarray(0, good.byteLength - 8)],
      ["truncated head", good.subarray(8)],
      ["empty", new Uint8Array()],
      ["zip64 locator", buildZip(entries, { zip64Locator: true })],
      ["entry count drift", buildZip(entries, { entriesTotalOverride: 9 })],
      ["declared archive comment", buildZip(entries, { trailingBytes: 4 })],
      ["undeclared appended bytes", new Uint8Array([...good, 1, 2, 3])],
    ];
    for (const [name, bytes] of cases) {
      const result = readPaperZipDirectory(bytes);
      expect(result.ok, name).toBe(false);
    }
  });

  it("refuses compression, encryption and deferred sizes", () => {
    const entries = canonicalEntries();
    for (const options of [
      { method: 8 },
      { flags: 0x0001 },
      { flags: 0x0008 },
      { flags: 0x0040 },
      { centralExtra: 4 },
      { centralComment: 4 },
    ]) {
      expect(readPaperZipDirectory(buildZip(entries, options)).ok).toBe(false);
    }
  });

  it("refuses a local-header extra field even when everything else agrees", async () => {
    const entries = canonicalEntries();
    const bytes = buildZip(entries, { localExtra: 9 });
    // Everything else is rebuilt around the extra field: offsets, the whole
    // byte count, the whole digest and every member size and digest agree.
    const ready = await readyFor(bytes, entries);
    expect(ready.artifact_bytes).toBe(bytes.byteLength);

    const directory = readPaperZipDirectory(bytes);
    expect(directory.ok).toBe(false);
    if (!directory.ok) {
      expect(directory.detail).toContain("額外欄位");
    }
    const verdict = await verifyPaperReviewArtifact(downloadInput(bytes, ready));
    expect(verdict.ok).toBe(false);

    // Control: the very same fixture without the extra field is accepted, so
    // the refusal is the local extra profile and nothing earlier.
    const clean = buildZip(entries);
    const cleanReady = await readyFor(clean, entries);
    expect((await verifyPaperReviewArtifact(downloadInput(clean, cleanReady))).ok).toBe(
      true,
    );
  });

  it("refuses every unsafe member path", () => {
    for (const path of [
      "../escape.json",
      "baseline/../../escape.json",
      "/absolute.json",
      "C:/drive.json",
      "back\\slash.json",
      "trailing/",
      "./dot.json",
      "double//slash.json",
    ]) {
      const bytes = buildZip([{ path, content: memberContent(path) }]);
      const result = readPaperZipDirectory(bytes);
      expect(result.ok, path).toBe(false);
    }
  });
});

describe("artifact verification", () => {
  it("accepts the canonical artifact and returns the verified digest", async () => {
    const { bytes, ready } = await canonicalArtifact();
    const result = await verifyPaperReviewArtifact(downloadInput(bytes, ready));
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.filename).toBe(FILENAME);
      expect(result.value.sha256).toBe(ready.artifact_sha256);
      expect(result.value.members.map((member) => member.path)).toEqual([
        ...PATHS,
      ]);
    }
  });

  it("refuses a wrong status, type, disposition or filename", async () => {
    const { bytes, ready } = await canonicalArtifact();
    for (const overrides of [
      { status: 409 },
      { status: 204 },
      { contentType: "application/octet-stream" },
      { contentType: null },
      { contentDisposition: null },
      { contentDisposition: "attachment" },
      { contentDisposition: 'attachment; filename="other.zip"' },
      { contentDisposition: `inline; filename="${FILENAME}"` },
    ]) {
      const result = await verifyPaperReviewArtifact(
        downloadInput(bytes, ready, overrides),
      );
      expect(result.ok, JSON.stringify(overrides)).toBe(false);
    }
  });

  it("refuses a total byte count or whole digest that drifts", async () => {
    const { bytes, ready } = await canonicalArtifact();
    const wrongBytes = await verifyPaperReviewArtifact(
      downloadInput(bytes, { ...ready, artifact_bytes: ready.artifact_bytes + 1 }),
    );
    expect(wrongBytes.ok).toBe(false);
    const wrongSha = await verifyPaperReviewArtifact(
      downloadInput(bytes, {
        ...ready,
        artifact_sha256:
          "0000000000000000000000000000000000000000000000000000000000000000",
      }),
    );
    expect(wrongSha.ok).toBe(false);
  });

  it("refuses reordered, missing, extra and duplicated members", async () => {
    const entries = canonicalEntries();

    const reordered = [...entries];
    [reordered[1], reordered[2]] = [reordered[2], reordered[1]];
    const reorderedBytes = buildZip(reordered);
    const reorderedReady = await readyFor(reorderedBytes, reordered);
    expect(
      (
        await verifyPaperReviewArtifact(
          downloadInput(reorderedBytes, reorderedReady),
        )
      ).ok,
    ).toBe(false);

    const missing = entries.slice(0, 9);
    const missingBytes = buildZip(missing);
    const missingReady = await readyFor(missingBytes, missing);
    expect(
      (await verifyPaperReviewArtifact(downloadInput(missingBytes, missingReady)))
        .ok,
    ).toBe(false);

    const extra = [
      ...entries,
      { path: "extra/eleventh.json", content: memberContent("extra") },
    ];
    const extraBytes = buildZip(extra);
    const extraReady = await readyFor(extraBytes, extra);
    expect(
      (await verifyPaperReviewArtifact(downloadInput(extraBytes, extraReady))).ok,
    ).toBe(false);

    const duplicated = [...entries.slice(0, 9), { ...entries[0] }];
    const duplicatedBytes = buildZip(duplicated);
    const duplicatedReady = await readyFor(duplicatedBytes, duplicated);
    expect(
      (
        await verifyPaperReviewArtifact(
          downloadInput(duplicatedBytes, duplicatedReady),
        )
      ).ok,
    ).toBe(false);
  });

  it("refuses a member whose declared bytes drift from the record", async () => {
    const entries = canonicalEntries();
    const bytes = buildZip(entries);
    const ready = await readyFor(bytes, entries);
    const drifted: PaperReviewReady = {
      ...ready,
      members: ready.members.map((member, index) =>
        index === 3 ? { ...member, bytes: member.bytes + 1 } : member,
      ),
    };
    expect(
      (await verifyPaperReviewArtifact(downloadInput(bytes, drifted))).ok,
    ).toBe(false);
  });

  it("refuses a member whose stored content digest drifts", async () => {
    const entries = canonicalEntries();
    // Same length, different bytes: only the digest can catch this one.
    const tampered = entries.map((entry, index) => {
      if (index !== 5) {
        return entry;
      }
      const content = memberContent(entry.path);
      content[content.byteLength - 2] ^= 0x01;
      return { path: entry.path, content };
    });
    const bytes = buildZip(tampered);
    // The record still describes the untampered member, and the whole-file
    // digest is recomputed, so only the per-member check can catch this.
    const ready = await readyFor(bytes, tampered);
    const honest = await readyFor(buildZip(entries), entries);
    const mixed: PaperReviewReady = {
      ...ready,
      members: ready.members.map((member, index) =>
        index === 5 ? honest.members[5] : member,
      ),
    };
    const result = await verifyPaperReviewArtifact(downloadInput(bytes, mixed));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.detail).toContain(PATHS[5]);
    }
  });

  it("refuses a declared size that does not match the stored size", async () => {
    const entries = canonicalEntries();
    const bytes = buildZip(entries, {
      declared: (index, actual) => (index === 2 ? actual + 3 : actual),
    });
    const ready = await readyFor(bytes, entries);
    const inflated: PaperReviewReady = {
      ...ready,
      members: ready.members.map((member, index) =>
        index === 2 ? { ...member, bytes: member.bytes + 3 } : member,
      ),
    };
    const result = await verifyPaperReviewArtifact(
      downloadInput(bytes, inflated),
    );
    expect(result.ok).toBe(false);
  });

  it("refuses a compressed or encrypted member before extraction", async () => {
    const entries = canonicalEntries();
    for (const options of [{ method: 8 }, { flags: 0x0001 }]) {
      const bytes = buildZip(entries, options);
      const ready = await readyFor(bytes, entries);
      expect(
        (await verifyPaperReviewArtifact(downloadInput(bytes, ready))).ok,
      ).toBe(false);
    }
  });
});

describe("terminal opener verification", () => {
  const text = "persisted opener text";

  async function claim(
    overrides: Partial<PaperReviewTerminalOpenerClaim> = {},
  ): Promise<PaperReviewTerminalOpenerClaim> {
    const encoded = textBytes(text);
    return {
      schema: "paper_review_terminal_opener.v1",
      snapshot_id: SNAPSHOT_ID,
      text,
      bytes: encoded.byteLength,
      sha256: (await paperSha256Hex(encoded)) ?? "",
      ...overrides,
    };
  }

  it("returns the persisted text once every claim is proven", async () => {
    const { bytes, ready } = await canonicalArtifact();
    expect(bytes.byteLength).toBeGreaterThan(0);
    const result = await verifyPaperReviewOpener({
      claim: await claim(),
      ready,
      snapshotId: SNAPSHOT_ID,
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value.text).toBe(text);
    }
  });

  it("refuses another snapshot, drifted bytes and drifted digests", async () => {
    const { ready } = await canonicalArtifact();
    const cases = [
      {
        name: "other snapshot",
        claim: await claim({
          snapshot_id: "paper-review-00000000000000000000000000000000",
        }),
        ready,
      },
      {
        name: "bytes drift",
        claim: await claim({ bytes: 999 }),
        ready,
      },
      {
        name: "response digest drift",
        claim: await claim({
          sha256:
            "1111111111111111111111111111111111111111111111111111111111111111",
        }),
        ready,
      },
      {
        name: "ready claim drift",
        claim: await claim(),
        ready: {
          ...ready,
          terminal_opener: { ...ready.terminal_opener, bytes: 1 },
        },
      },
      {
        name: "ready digest drift",
        claim: await claim(),
        ready: {
          ...ready,
          terminal_opener: {
            ...ready.terminal_opener,
            sha256:
              "2222222222222222222222222222222222222222222222222222222222222222",
          },
        },
      },
    ];
    for (const item of cases) {
      const result = await verifyPaperReviewOpener({
        claim: item.claim,
        ready: item.ready,
        snapshotId: SNAPSHOT_ID,
      });
      expect(result.ok, item.name).toBe(false);
    }
  });

  it("refuses a text that no longer matches its own digest", async () => {
    const { ready } = await canonicalArtifact();
    const tampered = await claim({ text: `${text} 加咗一句` });
    const result = await verifyPaperReviewOpener({
      claim: tampered,
      ready,
      snapshotId: SNAPSHOT_ID,
    });
    expect(result.ok).toBe(false);
  });

  it("refuses a lie about the text even when the record repeats it", async () => {
    const { ready } = await canonicalArtifact();

    // The response under-reports its own byte count and the ready record
    // repeats the same number, so only measuring the text can catch it.
    const shortCount = await claim({ bytes: 4 });
    const agreeingOnBytes = await verifyPaperReviewOpener({
      claim: shortCount,
      ready: {
        ...ready,
        terminal_opener: { bytes: 4, sha256: shortCount.sha256 },
      },
      snapshotId: SNAPSHOT_ID,
    });
    expect(agreeingOnBytes.ok).toBe(false);

    // Same for a digest both sides agree on but the text does not produce.
    const wrongDigest = await claim({
      sha256:
        "3333333333333333333333333333333333333333333333333333333333333333",
    });
    const agreeingOnDigest = await verifyPaperReviewOpener({
      claim: wrongDigest,
      ready: {
        ...ready,
        terminal_opener: {
          bytes: wrongDigest.bytes,
          sha256: wrongDigest.sha256,
        },
      },
      snapshotId: SNAPSHOT_ID,
    });
    expect(agreeingOnDigest.ok).toBe(false);
  });

  it("refuses an unsafe current snapshot identity", async () => {
    const { ready } = await canonicalArtifact();
    const result = await verifyPaperReviewOpener({
      claim: await claim(),
      ready,
      snapshotId: "../escape",
    });
    expect(result.ok).toBe(false);
  });
});
