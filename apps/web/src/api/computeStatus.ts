/** System compute architecture + error types (Phase E). */

export type ComputeBackendName = "python" | "rust" | string;

export interface ComputeErrorRecord {
  id: string;
  schema?: string;
  feature: string;
  severity: "error" | "warn" | "info" | string;
  message: string;
  detail: string;
  tip: string;
  effective_backend?: string | null;
  requested_backend?: string | null;
  fallback_used?: boolean;
  writes_authority?: boolean;
  context?: Record<string, unknown>;
  at: string;
}

export interface ComputeStatus {
  schema: string;
  health?: "ok" | "warn" | "error" | string;
  production_default_backend: string;
  requested_backend: string;
  effective_chart_backend: ComputeBackendName;
  effective_backtest_backend?: ComputeBackendName;
  fallback_reason?: string | null;
  rust_globally_disabled: boolean;
  dylib_path?: string | null;
  rust_admitted: boolean;
  rust_admit_detail?: string | null;
  product_chart_will_try_rust: boolean;
  product_backtest_will_try_rust?: boolean;
  writes_authority: boolean;
  architecture?: {
    authority: string;
    compute: string;
    summary_zh: string;
  };
  authority_owners: Record<string, string>;
  recent_errors?: ComputeErrorRecord[];
  error_count?: number;
  checked_at: string;
}

export interface ComputeErrorList {
  schema: string;
  count: number;
  errors: ComputeErrorRecord[];
  checked_at: string;
}
