// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/broker/broker-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/broker/broker-api")>();
  return { ...actual, getPositions: vi.fn(), getOrders: vi.fn(), createOrder: vi.fn() };
});

import {
  createOrder,
  getOrders,
  getPositions,
  type AlpacaOrder,
  type AlpacaPosition,
} from "@/lib/broker/broker-api";
import { PositionsView } from "./positions-view";

const mockPositions = vi.mocked(getPositions);
const mockOrders = vi.mocked(getOrders);
const mockCreateOrder = vi.mocked(createOrder);

beforeEach(() => {
  mockPositions.mockReset();
  mockOrders.mockReset();
  mockCreateOrder.mockReset();
  mockOrders.mockResolvedValue([]);
});

afterEach(() => cleanup());

describe("PositionsView", () => {
  it("renders a positions row with an unrealized-P/L badge", async () => {
    mockPositions.mockResolvedValue([
      {
        symbol: "AAPL",
        qty: "10",
        side: "long",
        market_value: "3350",
        cost_basis: "3000",
        unrealized_pl: "350",
        unrealized_plpc: "0.1167",
        current_price: "335",
        avg_entry_price: "300",
      },
    ]);

    render(<PositionsView />);

    expect(await screen.findByText("AAPL")).toBeTruthy();
    expect(screen.getByText(/\+\$350\.00/)).toBeTruthy();
    expect(screen.getByText(/\+11\.67%/)).toBeTruthy();
  });

  it("shows empty states for no positions and no orders", async () => {
    mockPositions.mockResolvedValue([]);

    render(<PositionsView />);

    expect(await screen.findByText("No open positions.")).toBeTruthy();
    expect(screen.getByText("No open orders.")).toBeTruthy();
  });

  it("shows the connect notice when the account is not connected", async () => {
    mockPositions.mockRejectedValue(
      new ApiError(503, "Service Unavailable", { detail: "No Alpaca account connected" }, "http://x"),
    );

    render(<PositionsView />);

    expect(await screen.findByText(/no alpaca paper account connected/i)).toBeTruthy();
  });

  it("surfaces a load error distinctly", async () => {
    mockPositions.mockRejectedValue(new ApiError(500, "Server Error", { detail: "kaboom" }, "http://x"));

    render(<PositionsView />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("kaboom");
  });

  it("renders an open order row", async () => {
    mockPositions.mockResolvedValue([]);
    mockOrders.mockResolvedValue([
      {
        id: "o1",
        client_order_id: "cid-1",
        symbol: "NVDA",
        qty: "5",
        filled_qty: "0",
        average_fill_price: null,
        side: "buy",
        type: "market",
        time_in_force: "day",
        status: "new",
        submitted_at: "2026-11-02T01:00:00Z",
        filled_at: null,
      },
    ]);

    render(<PositionsView />);

    expect(await screen.findByText("NVDA")).toBeTruthy();
    // Scope to the orders table so the manual-order ticket's BUY option (added in
    // #143) doesn't collide with the order row's side badge.
    const ordersTable = screen.getByRole("table");
    expect(within(ordersTable).getByText("BUY")).toBeTruthy();
  });
});

function position(overrides: Partial<AlpacaPosition> = {}): AlpacaPosition {
  return {
    symbol: "AAPL",
    qty: "10",
    side: "long",
    market_value: "3350",
    cost_basis: "3000",
    unrealized_pl: "350",
    unrealized_plpc: "0.1167",
    current_price: "335",
    avg_entry_price: "300",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Fire the 15 s auto-refresh. */
async function advancePoll() {
  await act(async () => {
    vi.advanceTimersByTime(15_000);
  });
}

function livePill(): HTMLElement {
  return screen.getByRole("button", { name: /live · every 15s/i });
}

describe("PositionsView live refresh in place (#164)", () => {
  beforeEach(() => {
    // Only the poll interval and the clock are faked; RTL's findBy* keeps real timeouts.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date(2026, 10, 9, 14, 41, 5));
    mockPositions.mockResolvedValue([position()]);
  });
  afterEach(() => vi.useRealTimers());

  it("shows the placeholder only on the first load", async () => {
    const first = deferred<AlpacaPosition[]>();
    mockPositions.mockReturnValueOnce(first.promise);
    render(<PositionsView />);
    expect(screen.getByText("Loading positions...")).toBeTruthy();

    await act(async () => first.resolve([position()]));
    expect(await screen.findByText("AAPL")).toBeTruthy();
    expect(screen.queryByText("Loading positions...")).toBeNull();
  });

  it("keeps the content on screen while a refresh is pending, then updates in place", async () => {
    render(<PositionsView />);
    expect(await screen.findByText("$335.00")).toBeTruthy();

    const refresh = deferred<AlpacaPosition[]>();
    mockPositions.mockReturnValueOnce(refresh.promise);
    await advancePoll();

    expect(mockPositions).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Loading positions/)).toBeNull();
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("$335.00")).toBeTruthy();
    // The Live pill marks the refresh in flight without changing its label.
    expect(livePill().getAttribute("aria-busy")).toBe("true");

    await act(async () => refresh.resolve([position({ current_price: "340", market_value: "3400" })]));
    expect(await screen.findByText("$340.00")).toBeTruthy();
    expect(screen.queryByText("$335.00")).toBeNull();
    expect(livePill().getAttribute("aria-busy")).toBeNull();
  });

  it("keeps the manual ticket's fields and its Confirm override step across a refresh", async () => {
    render(<PositionsView />);
    await screen.findByText("AAPL");

    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "msft" } });
    fireEvent.change(screen.getByLabelText(/quantity/i), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Review manual order" }));
    expect(screen.getByRole("button", { name: "Confirm override" })).toBeTruthy();

    const refresh = deferred<AlpacaPosition[]>();
    mockPositions.mockReturnValueOnce(refresh.promise);
    await advancePoll();
    await act(async () => refresh.resolve([position({ current_price: "340", market_value: "3400" })]));
    await screen.findByText("$340.00");

    expect((screen.getByLabelText("Symbol") as HTMLInputElement).value).toBe("msft");
    expect((screen.getByLabelText(/quantity/i) as HTMLInputElement).value).toBe("3");
    expect(screen.getByRole("button", { name: "Confirm override" })).toBeTruthy();
    expect(screen.getByText(/submit manual/i).textContent).toContain("MSFT");
    expect(mockCreateOrder).not.toHaveBeenCalled();
  });

  it("keeps an open Close confirmation across a refresh", async () => {
    render(<PositionsView />);
    await screen.findByText("AAPL");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByText("SELL 10 (override)?")).toBeTruthy();

    const refresh = deferred<AlpacaPosition[]>();
    mockPositions.mockReturnValueOnce(refresh.promise);
    await advancePoll();
    await act(async () => refresh.resolve([position({ current_price: "340", market_value: "3400" })]));
    await screen.findByText("$340.00");

    expect(screen.getByText("SELL 10 (override)?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("reloads in place after a manual order, keeping the ticket's confirmation message", async () => {
    mockCreateOrder.mockResolvedValue({ id: "o9", status: "accepted" } as AlpacaOrder);
    render(<PositionsView />);
    await screen.findByText("AAPL");

    const reload = deferred<AlpacaPosition[]>();
    mockPositions.mockReturnValueOnce(reload.promise);
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "AAPL" } });
    fireEvent.click(screen.getByRole("button", { name: "Review manual order" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm override" }));

    expect(await screen.findByText(/manual buy 1 aapl submitted/i)).toBeTruthy();
    expect(mockPositions).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Loading positions/)).toBeNull();
    expect(screen.getByText("$335.00")).toBeTruthy();

    await act(async () => reload.resolve([position({ qty: "11", market_value: "3685" })]));
    expect(await screen.findByText("$3,685.00")).toBeTruthy();
    expect(screen.getByText(/manual buy 1 aapl submitted/i)).toBeTruthy();
  });

  it("keeps the last good data with a non-blocking note when a refresh fails", async () => {
    render(<PositionsView />);
    await screen.findByText("$335.00");

    vi.setSystemTime(new Date(2026, 10, 9, 14, 45, 0));
    mockPositions.mockRejectedValueOnce(new ApiError(500, "Server Error", { detail: "kaboom" }, "http://x"));
    await advancePoll();

    const note = await screen.findByText(/couldn't refresh/i);
    expect(note.textContent).toMatch(/showing data from 2:41:05/);
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("$335.00")).toBeTruthy();
    expect(screen.queryByText(/kaboom/)).toBeNull(); // not the section-level error
    expect(screen.queryByText(/Loading positions/)).toBeNull();

    // The next good refresh clears the note.
    mockPositions.mockResolvedValueOnce([position({ current_price: "340", market_value: "3400" })]);
    await advancePoll();
    expect(await screen.findByText("$340.00")).toBeTruthy();
    expect(screen.queryByText(/couldn't refresh/i)).toBeNull();
  });

  it("still shows the error when there was never any good data", async () => {
    mockPositions.mockRejectedValue(new ApiError(500, "Server Error", { detail: "kaboom" }, "http://x"));
    render(<PositionsView />);
    expect((await screen.findByRole("alert")).textContent).toContain("kaboom");

    mockPositions.mockRejectedValue(new ApiError(500, "Server Error", { detail: "still down" }, "http://x"));
    await advancePoll();
    expect(await screen.findByText(/still down/)).toBeTruthy();
    expect(screen.queryByText(/couldn't refresh/i)).toBeNull();
  });
});
