/**
 * Toast context and hook, kept out of the component file so Fast Refresh can
 * still hot-swap the components (same split as `pageTabsSlot`).
 */

import { createContext, useContext } from "react";

import type { HumanError } from "../../lib/net/errorMessage";

export type ToastTone = "error" | "success";

export interface ToastAction {
  label: string;
  run: () => void;
}

export interface ToastRecord {
  id: string;
  tone: ToastTone;
  title: string;
  hint: string;
  detail: string;
  action?: ToastAction;
}

export interface ToastApi {
  showError: (error: HumanError, action?: ToastAction) => void;
  showSuccess: (title: string) => void;
  dismiss: (id: string) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return api;
}

const NO_TOASTS: ToastApi = {
  showError: () => undefined,
  showSuccess: () => undefined,
  dismiss: () => undefined,
};

/**
 * For pages, which `AppShell` always wraps in a provider but which the test
 * suite renders on their own. Dropping a confirmation in an isolated render is
 * the right trade; making every existing page test mount the shell is not.
 */
export function useOptionalToast(): ToastApi {
  return useContext(ToastContext) ?? NO_TOASTS;
}
