import { describe, expect, it } from "vitest";
import {
  formatDateTime,
  formatMoney,
  formatPercent,
  formatSignedMoney,
  signTone,
  toNumber,
} from "./format";

describe("toNumber", () => {
  it("parses strings and numbers, rejects junk/empty/null", () => {
    expect(toNumber("100000")).toBe(100000);
    expect(toNumber(42)).toBe(42);
    expect(toNumber("")).toBeNull();
    expect(toNumber(null)).toBeNull();
    expect(toNumber(undefined)).toBeNull();
    expect(toNumber("nope")).toBeNull();
  });
});

describe("formatMoney", () => {
  it("formats currency and renders an em dash for missing values", () => {
    expect(formatMoney("100000")).toBe("$100,000.00");
    expect(formatMoney(1234.5)).toBe("$1,234.50");
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("bad")).toBe("—");
  });
});

describe("formatSignedMoney", () => {
  it("prefixes a + for gains and keeps - for losses", () => {
    expect(formatSignedMoney("1000")).toBe("+$1,000.00");
    expect(formatSignedMoney(-80)).toBe("-$80.00");
    expect(formatSignedMoney(0)).toBe("$0.00");
    expect(formatSignedMoney(null)).toBe("—");
  });
});

describe("formatPercent", () => {
  it("treats the input as a fraction and signs it", () => {
    expect(formatPercent(0.0525)).toBe("+5.25%");
    expect(formatPercent(-0.01)).toBe("-1.00%");
    expect(formatPercent(null)).toBe("—");
  });
});

describe("signTone", () => {
  it("maps sign to tone", () => {
    expect(signTone(5)).toBe("positive");
    expect(signTone("-3")).toBe("negative");
    expect(signTone(0)).toBe("neutral");
    expect(signTone(null)).toBe("neutral");
  });
});

describe("formatDateTime", () => {
  it("renders an em dash for missing and echoes unparseable input", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
    expect(formatDateTime("2026-11-02T01:00:00Z")).not.toBe("—");
  });
});
