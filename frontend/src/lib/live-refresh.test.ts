import { describe, expect, it } from "vitest";

import {
  MAX_REFRESH_AGE_MS,
  formatClockTime,
  keepLastGood,
  shouldStartPoll,
  staleDataMessage,
} from "./live-refresh";

describe("shouldStartPoll", () => {
  it("starts a refresh when none is outstanding", () => {
    expect(shouldStartPoll(null, 1_000)).toBe(true);
  });

  it("skips while the outstanding refresh is younger than the age limit", () => {
    expect(shouldStartPoll(0, 15_000)).toBe(false);
    expect(shouldStartPoll(0, MAX_REFRESH_AGE_MS - 1)).toBe(false);
  });

  it("supersedes an outstanding refresh once it reaches the age limit", () => {
    expect(MAX_REFRESH_AGE_MS).toBe(45_000);
    expect(shouldStartPoll(0, MAX_REFRESH_AGE_MS)).toBe(true);
    expect(shouldStartPoll(10_000, 70_000)).toBe(true);
    expect(shouldStartPoll(10_000, 20_000, 5_000)).toBe(true);
  });
});

function fulfilled<T>(value: T): PromiseSettledResult<T> {
  return { status: "fulfilled", value };
}

function rejected<T>(reason: unknown): PromiseSettledResult<T> {
  return { status: "rejected", reason };
}

describe("keepLastGood", () => {
  it("takes the new value when the request succeeded", () => {
    expect(keepLastGood(fulfilled([2]), [1])).toEqual({ value: [2], kept: false });
    expect(keepLastGood(fulfilled<number[]>([]), [1])).toEqual({ value: [], kept: false });
  });

  it("keeps the previous good value when the request failed", () => {
    expect(keepLastGood(rejected<number[]>(new Error("down")), [1])).toEqual({
      value: [1],
      kept: true,
    });
  });

  it("has nothing to keep when there was no previous value", () => {
    expect(keepLastGood(rejected<number[]>(new Error("down")), null)).toEqual({
      value: null,
      kept: false,
    });
  });
});

describe("staleDataMessage", () => {
  it("names the time of the data still on screen", () => {
    const at = new Date(2026, 10, 9, 14, 41, 5).getTime();
    expect(formatClockTime(at)).toMatch(/2:41:05/);
    expect(staleDataMessage(at)).toBe(`Couldn't refresh — showing data from ${formatClockTime(at)}.`);
  });

  it("has a fallback without a timestamp", () => {
    expect(staleDataMessage(null)).toBe("Couldn't refresh — showing older data.");
  });
});
