import { describe, expect, it } from "vitest";
import {
  ageSeconds,
  estimatedMarketStatus,
  formatAge,
  freshness,
  latestBarTimestamp,
  STALE_THRESHOLD_SECONDS,
} from "./freshness";
import type { OHLCVBar } from "./market-data-api";

function bar(timestamp: string): OHLCVBar {
  return { timestamp, open: 1, high: 1, low: 1, close: 1, volume: 1 };
}

describe("latestBarTimestamp", () => {
  it("returns the last bar's timestamp, or null when empty", () => {
    expect(latestBarTimestamp([])).toBeNull();
    expect(latestBarTimestamp(null)).toBeNull();
    expect(latestBarTimestamp([bar("2026-11-01T00:00:00Z"), bar("2026-11-02T00:00:00Z")])).toBe(
      "2026-11-02T00:00:00Z",
    );
  });
});

describe("ageSeconds", () => {
  it("is null for missing/unparseable timestamps", () => {
    expect(ageSeconds(null, Date.now())).toBeNull();
    expect(ageSeconds("not-a-date", Date.now())).toBeNull();
  });

  it("measures seconds since the timestamp, treating naive times as UTC", () => {
    const now = Date.parse("2026-11-02T00:10:00Z");
    expect(ageSeconds("2026-11-02T00:00:00Z", now)).toBe(600);
    expect(ageSeconds("2026-11-02T00:00:00", now)).toBe(600); // no zone -> UTC
  });
});

describe("freshness", () => {
  it("is FRESH at or under the threshold and STALE above it", () => {
    expect(freshness(0)).toBe("FRESH");
    expect(freshness(STALE_THRESHOLD_SECONDS)).toBe("FRESH");
    expect(freshness(STALE_THRESHOLD_SECONDS + 1)).toBe("STALE");
  });

  it("is UNKNOWN when the age is null", () => {
    expect(freshness(null)).toBe("UNKNOWN");
  });
});

describe("formatAge", () => {
  it("renders compact units and an em dash for null", () => {
    expect(formatAge(null)).toBe("—");
    expect(formatAge(30)).toBe("30s");
    expect(formatAge(90)).toBe("1m");
    expect(formatAge(7200)).toBe("2h");
    expect(formatAge(172800)).toBe("2d");
  });
});

describe("estimatedMarketStatus", () => {
  it("is OPEN during US regular hours on a weekday (EST)", () => {
    // 2026-11-02 is a Monday; 15:00 UTC = 10:00 ET (EST, UTC-5).
    expect(estimatedMarketStatus(new Date("2026-11-02T15:00:00Z"))).toBe("OPEN");
  });

  it("is CLOSED before the open, after the close, and on weekends", () => {
    expect(estimatedMarketStatus(new Date("2026-11-02T13:00:00Z"))).toBe("CLOSED"); // 08:00 ET
    expect(estimatedMarketStatus(new Date("2026-11-02T22:00:00Z"))).toBe("CLOSED"); // 17:00 ET
    expect(estimatedMarketStatus(new Date("2026-11-07T15:00:00Z"))).toBe("CLOSED"); // Saturday
  });
});
