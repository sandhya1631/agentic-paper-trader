// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { usePolling } from "./use-polling";

function Poller({ fn, ms, enabled }: { fn: () => void; ms: number; enabled: boolean }) {
  usePolling(fn, ms, enabled);
  return null;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePolling", () => {
  it("calls the callback once per interval while enabled", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} ms={1000} enabled />);
    expect(fn).toHaveBeenCalledTimes(0); // does not fire immediately
    vi.advanceTimersByTime(3000);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("does not fire when disabled", () => {
    const fn = vi.fn();
    render(<Poller fn={fn} ms={1000} enabled={false} />);
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(0);
  });

  it("stops firing after unmount", () => {
    const fn = vi.fn();
    const { unmount } = render(<Poller fn={fn} ms={1000} enabled />);
    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(1);
    unmount();
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
