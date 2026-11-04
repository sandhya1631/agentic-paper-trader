/**
 * Meta API seam — `GET /meta/constants` (#139): the backend enum values and limits the UI
 * renders (e.g. the approval modes and the watchlist size cap), so the frontend does not
 * hardcode values that could drift from the server.
 */
import { api } from "@/lib/api/client";

export interface MetaConstants {
  currency: string;
  cycle_trigger: string[];
  cycle_status: string[];
  decision_action: string[];
  policy_decision_type: string[];
  approval_mode: string[];
  approval_status: string[];
  /** Most symbols an agent's watchlist may hold (the backend rejects more with 422). */
  max_watchlist_size: number;
}

export function getMetaConstants(): Promise<MetaConstants> {
  return api.get<MetaConstants>("/meta/constants");
}
