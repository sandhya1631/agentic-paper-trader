/**
 * Backend constants/enums (#139): read these instead of hardcoding enum strings
 * that would silently go stale if the backend ever renames or adds a value.
 */
import { api } from "@/lib/api/client";

export interface Constants {
  currency: string;
  cycle_trigger: string[];
  cycle_status: string[];
  decision_action: string[];
  policy_decision_type: string[];
  approval_mode: string[];
  approval_status: string[];
}

export function getConstants(): Promise<Constants> {
  return api.get<Constants>("/meta/constants");
}
