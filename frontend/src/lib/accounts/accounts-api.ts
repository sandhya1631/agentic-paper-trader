/**
 * Per-user Alpaca account connection. Secrets are write-only: they are POSTed to be
 * encrypted at rest and never returned by any endpoint.
 */
import { api } from "@/lib/api/client";

export interface TradingAccount {
  id: string;
  provider: string;
  is_paper: boolean;
  is_connected: boolean;
  last_verified_at: string | null;
}

export interface ConnectAccountInput {
  api_key: string;
  api_secret: string;
  is_paper: boolean;
}

/** Current account status; rejects with 404 when nothing is connected. */
export function getAccountStatus(): Promise<TradingAccount> {
  return api.get<TradingAccount>("/accounts/alpaca");
}

export function connectAccount(input: ConnectAccountInput): Promise<TradingAccount> {
  return api.post<TradingAccount>("/accounts/alpaca", input);
}

export function disconnectAccount(): Promise<void> {
  return api.delete<void>("/accounts/alpaca");
}
