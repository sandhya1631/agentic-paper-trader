import { describe, expect, it } from "vitest";
import { formatOrderQuantity } from "./order-view";

describe("formatOrderQuantity", () => {
  it("shows the share quantity of a regular order", () => {
    expect(formatOrderQuantity({ qty: "5", notional: null })).toBe("5");
  });

  it("shows the dollar amount of a notional order (qty null)", () => {
    expect(formatOrderQuantity({ qty: null, notional: "100" })).toBe("$100.00 notional");
    expect(formatOrderQuantity({ qty: null, notional: "1250.5" })).toBe("$1,250.50 notional");
  });

  it("renders an em dash when neither is present", () => {
    expect(formatOrderQuantity({ qty: null, notional: null })).toBe("—");
  });
});
