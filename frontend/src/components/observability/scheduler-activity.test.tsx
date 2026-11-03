// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/agent/observability-api", () => ({ getSchedulerLogs: vi.fn() }));
vi.mock("@/lib/agent/cycles-api", () => ({ listCycles: vi.fn() }));

import { getSchedulerLogs } from "@/lib/agent/observability-api";
import { listCycles } from "@/lib/agent/cycles-api";
import { SchedulerActivity } from "./scheduler-activity";

const mockLogs = vi.mocked(getSchedulerLogs);
const mockCycles = vi.mocked(listCycles);

beforeEach(() => {
  mockLogs.mockReset();
  mockCycles.mockReset();
  mockCycles.mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0, next_offset: null });
});
afterEach(() => cleanup());

describe("SchedulerActivity", () => {
  it("renders skipped and failed tick rows with reasons", async () => {
    mockLogs.mockResolvedValue([
      {
        schedule_id: "s1",
        tick: {
          status: "SKIPPED",
          reason: "market_closed",
          started_at: "2026-11-02T14:00:00Z",
        },
      },
      {
        schedule_id: "s1",
        tick: {
          status: "FAILED",
          reason: "agent_cycle_failed",
          started_at: "2026-11-02T14:15:00Z",
          attempted_agents: 1,
          successful_agents: 0,
          failed_agents: 1,
        },
      },
    ]);

    render(<SchedulerActivity />);

    expect(await screen.findByText("Skipped")).toBeTruthy();
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("Market closed")).toBeTruthy();
    expect(screen.getByText("Agent cycle failed")).toBeTruthy();
  });

  it("shows an empty state when no ticks have run", async () => {
    mockLogs.mockResolvedValue([]);
    render(<SchedulerActivity />);
    expect(await screen.findByText(/No scheduler ticks recorded yet/)).toBeTruthy();
  });

  it("surfaces a load error distinctly", async () => {
    mockLogs.mockRejectedValue(new ApiError(500, "Server Error", { detail: "logs down" }, "http://x"));
    render(<SchedulerActivity />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("logs down");
  });
});
