import { describe, expect, it } from "vitest";

import {
  PAPER_REVIEW_REQUEST_QUERY_KEY,
  PAPER_TRADER_QUERY_KEY,
  hasPartialPaperReviewUrlIdentity,
  isPaperReviewLocked,
  isPaperReviewRequestId,
  isSafePaperTraderId,
  paperReviewUrlIdentityMatches,
  readPaperReviewUrlIdentity,
  writePaperReviewUrlIdentity,
} from "./reviewState";

const TRADER_ID = "trader-1f2e3d4c5b6a798877665544332211ff";
const REQUEST_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const IDENTITY = { trader_id: TRADER_ID, request_id: REQUEST_ID };

describe("Stage B URL identity helpers", () => {
  it("reads both halves of a valid identity", () => {
    const identity = readPaperReviewUrlIdentity(
      `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`,
    );
    expect(identity).toEqual(IDENTITY);
  });

  it("returns null when the query carries neither half", () => {
    expect(readPaperReviewUrlIdentity("?scenario=owner-review")).toBeNull();
    expect(hasPartialPaperReviewUrlIdentity("?scenario=owner-review")).toBe(
      false,
    );
  });

  it("fails closed on a missing half", () => {
    for (const search of [
      `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}`,
      `?${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`,
    ]) {
      expect(readPaperReviewUrlIdentity(search)).toBeNull();
      expect(hasPartialPaperReviewUrlIdentity(search)).toBe(true);
    }
  });

  it("fails closed on a duplicated authority for either half", () => {
    expect(
      readPaperReviewUrlIdentity(
        `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`,
      ),
    ).toBeNull();
    expect(
      readPaperReviewUrlIdentity(
        `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`,
      ),
    ).toBeNull();
  });

  it("fails closed on an unsafe trader identity or a non-canonical request id", () => {
    for (const traderId of [
      "../escape",
      "trader-XYZ",
      "trader-1F2E3D4C5B6A798877665544332211FF",
      "",
    ]) {
      expect(
        readPaperReviewUrlIdentity(
          `?${PAPER_TRADER_QUERY_KEY}=${traderId}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`,
        ),
      ).toBeNull();
    }
    for (const requestId of [
      REQUEST_ID.toUpperCase(),
      "00000000-0000-1000-8000-000000000000",
      "00000000-0000-4000-c000-000000000000",
      "not-a-uuid",
    ]) {
      expect(
        readPaperReviewUrlIdentity(
          `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${requestId}`,
        ),
      ).toBeNull();
    }
  });

  it("writes both halves once while keeping every other query key", () => {
    const next = writePaperReviewUrlIdentity(
      `?scenario=owner-review&${PAPER_TRADER_QUERY_KEY}=old&strategy=strategy-0003`,
      IDENTITY,
    );
    expect(next).not.toBeNull();
    if (next !== null) {
      expect(next.getAll(PAPER_TRADER_QUERY_KEY)).toEqual([TRADER_ID]);
      expect(next.getAll(PAPER_REVIEW_REQUEST_QUERY_KEY)).toEqual([REQUEST_ID]);
      expect(next.get("scenario")).toBe("owner-review");
      expect(next.get("strategy")).toBe("strategy-0003");
      expect(readPaperReviewUrlIdentity(next)).toEqual(IDENTITY);
    }
  });

  it("refuses to write an unsafe identity into the URL", () => {
    expect(
      writePaperReviewUrlIdentity("", {
        trader_id: "../escape",
        request_id: REQUEST_ID,
      }),
    ).toBeNull();
    expect(
      writePaperReviewUrlIdentity("", {
        trader_id: TRADER_ID,
        request_id: "not-a-uuid",
      }),
    ).toBeNull();
  });

  it("matches only the exact identity", () => {
    const search = `?${PAPER_TRADER_QUERY_KEY}=${TRADER_ID}&${PAPER_REVIEW_REQUEST_QUERY_KEY}=${REQUEST_ID}`;
    expect(paperReviewUrlIdentityMatches(search, IDENTITY)).toBe(true);
    expect(
      paperReviewUrlIdentityMatches(search, {
        ...IDENTITY,
        request_id: "00000000-0000-4000-8000-000000000000",
      }),
    ).toBe(false);
    expect(
      paperReviewUrlIdentityMatches(search, {
        ...IDENTITY,
        trader_id: "trader-00000000000000000000000000000000",
      }),
    ).toBe(false);
    expect(paperReviewUrlIdentityMatches("", IDENTITY)).toBe(false);
  });

  it("exposes the identity guards it uses", () => {
    expect(isSafePaperTraderId(TRADER_ID)).toBe(true);
    expect(isSafePaperTraderId("trader-../escape")).toBe(false);
    expect(isPaperReviewRequestId(REQUEST_ID)).toBe(true);
    expect(isPaperReviewRequestId(REQUEST_ID.toUpperCase())).toBe(false);
  });

  it("locks exactly the phases with no known terminal outcome", () => {
    expect(isPaperReviewLocked("submitting")).toBe(true);
    expect(isPaperReviewLocked("preparing")).toBe(true);
    expect(isPaperReviewLocked("recovering")).toBe(true);
    expect(isPaperReviewLocked("idle")).toBe(false);
    expect(isPaperReviewLocked("ready")).toBe(false);
    expect(isPaperReviewLocked("failed")).toBe(false);
    expect(isPaperReviewLocked("unknown")).toBe(false);
  });
});
