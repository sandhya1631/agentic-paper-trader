// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError } from "@/lib/api/errors";

vi.mock("@/lib/accounts/llm-key-api", () => ({
  getLlmKeyStatus: vi.fn(),
  connectLlmKey: vi.fn(),
  disconnectLlmKey: vi.fn(),
}));
vi.mock("@/lib/meta/meta-api", () => ({ getConstants: vi.fn() }));

import {
  connectLlmKey,
  disconnectLlmKey,
  getLlmKeyStatus,
  type LlmKeyStatus,
} from "@/lib/accounts/llm-key-api";
import { getConstants, type Constants } from "@/lib/meta/meta-api";
import { LlmKeyCard } from "./llm-key-card";

const mockGetStatus = vi.mocked(getLlmKeyStatus);
const mockConnect = vi.mocked(connectLlmKey);
const mockDisconnect = vi.mocked(disconnectLlmKey);
const mockConstants = vi.mocked(getConstants);

/** /meta/constants with only the field this card reads (#191). */
function constantsWith(llm_key_providers?: string[]): Constants {
  return { llm_key_providers } as Constants;
}

const SERVER: LlmKeyStatus = {
  connected: false,
  provider: null,
  last_verified_at: null,
  effective_source: "SERVER",
  effective_provider: "openai",
  effective_model: "gpt-4o-mini",
};
const USER: LlmKeyStatus = {
  connected: true,
  provider: "openai",
  last_verified_at: "2026-11-18T12:00:00Z",
  effective_source: "USER",
  effective_provider: "openai",
  effective_model: "gpt-4o-mini",
};
const KEY = "sk-test-not-a-real-key";

function keyField() {
  return screen.getByLabelText(/^provider api key$/i) as HTMLInputElement;
}

beforeEach(() => {
  mockGetStatus.mockReset();
  mockConnect.mockReset();
  mockDisconnect.mockReset();
  mockConstants.mockReset();
  mockConstants.mockResolvedValue(constantsWith(["openai", "anthropic"]));
});
afterEach(() => cleanup());

describe("LlmKeyCard", () => {
  it("shows the connect form and the server's key when nothing is connected", async () => {
    mockGetStatus.mockResolvedValue(SERVER);
    render(<LlmKeyCard />);

    expect(
      await screen.findByText("Cycles use: the server's key (OpenAI, gpt-4o-mini)"),
    ).toBeTruthy();
    expect(keyField().type).toBe("password");
    expect(screen.getByLabelText(/^provider$/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /disconnect/i })).toBeNull();
  });

  it("connects the chosen provider, clears the key field and shows the user's key", async () => {
    mockGetStatus.mockResolvedValueOnce(SERVER).mockResolvedValueOnce({
      ...USER,
      provider: "anthropic",
      effective_provider: "anthropic",
      effective_model: "claude-opus-5-5",
    });
    mockConnect.mockResolvedValue({
      provider: "anthropic",
      is_connected: true,
      last_verified_at: USER.last_verified_at,
    });
    render(<LlmKeyCard />);

    await screen.findByRole("option", { name: "Claude" });
    fireEvent.change(screen.getByLabelText(/^provider$/i), {
      target: { value: "anthropic" },
    });
    fireEvent.change(keyField(), { target: { value: `  ${KEY}  ` } });
    fireEvent.click(screen.getByRole("button", { name: /connect key/i }));

    expect(await screen.findByText("Cycles use: your Claude key (claude-opus-5-5)")).toBeTruthy();
    expect(mockConnect).toHaveBeenCalledWith({ provider: "anthropic", api_key: KEY });
    expect(screen.getByRole("button", { name: /disconnect/i })).toBeTruthy();
    expect(document.body.textContent).not.toContain(KEY);
  });

  it("shows the sanitized error and clears the key field when the key is rejected", async () => {
    mockGetStatus.mockResolvedValue(SERVER);
    mockConnect.mockRejectedValue(
      new ApiError(
        422,
        "Unprocessable Content",
        {
          detail:
            "OpenAI did not accept this key, so it was not saved: " +
            "OpenAI API unreachable: AuthenticationError (HTTP 401)",
        },
        "http://x/accounts/llm",
      ),
    );
    render(<LlmKeyCard />);

    fireEvent.change(await screen.findByLabelText(/^provider api key$/i), { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: /connect key/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("OpenAI did not accept this key");
    expect(keyField().value).toBe("");
    expect(screen.getByRole("button", { name: /connect key/i })).toBeTruthy();
  });

  it("disconnects a connected key and falls back to the server's key", async () => {
    mockGetStatus.mockResolvedValueOnce(USER).mockResolvedValueOnce(SERVER);
    mockDisconnect.mockResolvedValue(undefined);
    render(<LlmKeyCard />);

    expect(await screen.findByText("Cycles use: your OpenAI key (gpt-4o-mini)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /disconnect/i }));

    await waitFor(() => expect(mockDisconnect).toHaveBeenCalledTimes(1));
    expect(
      await screen.findByText("Cycles use: the server's key (OpenAI, gpt-4o-mini)"),
    ).toBeTruthy();
  });

  it("offers the providers from /meta/constants, in the backend's order", async () => {
    mockGetStatus.mockResolvedValue(SERVER);
    mockConstants.mockResolvedValue(constantsWith(["anthropic", "openai"]));
    mockConnect.mockResolvedValue({
      provider: "anthropic",
      is_connected: true,
      last_verified_at: USER.last_verified_at,
    });
    render(<LlmKeyCard />);

    await screen.findByRole("option", { name: "Claude" });
    const options = screen.getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => [o.value, o.textContent])).toEqual([
      ["anthropic", "Claude"],
      ["openai", "OpenAI"],
    ]);
    // Untouched, the select holds the backend's first provider, and that is what's sent.
    fireEvent.change(keyField(), { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: /connect key/i }));
    await waitFor(() =>
      expect(mockConnect).toHaveBeenCalledWith({ provider: "anthropic", api_key: KEY }),
    );
  });

  it("can't connect when the backend sends no providers", async () => {
    mockGetStatus.mockResolvedValue(SERVER);
    mockConstants.mockResolvedValue(constantsWith(undefined));
    render(<LlmKeyCard />);

    expect(
      await screen.findByText("Could not load the AI providers. Reload the page to connect a key."),
    ).toBeTruthy();
    fireEvent.change(keyField(), { target: { value: KEY } });
    const connect = screen.getByRole("button", { name: /connect key/i }) as HTMLButtonElement;
    expect(connect.disabled).toBe(true);
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });

  it("says cycles can't run when there is no key and no server fallback", async () => {
    mockGetStatus.mockResolvedValue({
      ...SERVER,
      effective_source: null,
      effective_provider: null,
      effective_model: null,
    });
    render(<LlmKeyCard />);

    expect(
      await screen.findByText("Cycles can't run: connect a key (the server's key is turned off)."),
    ).toBeTruthy();
  });
});
