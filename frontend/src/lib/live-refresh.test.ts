import { describe, expect, it } from "vitest";

import { formatClockTime, keepLastGood, staleDataMessage } from "./live-refresh";

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
