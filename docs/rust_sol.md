# rust_sol — Handoff for AI Review

**Project:** futures-research (`alog-trading-2-main`)  
**Date:** 2026-07-31  
**Architecture:** Python Authority + Rust Compute (ADR-0001)  
**Scope contract:** Chart compute vertical slice **landed** and **production default is `auto` (prefer Rust)**.  
**Audience:** Independent AI / human reviewer (do not expand scope past this document).

---

## 0. One-line truth

**Python owns all authority (IB, paper ledger, run seal, admission, Web).**  
**Rust is pure chart compute (`writes_authority=false`) with full-job Python fallback.**  
**Production default: `FR_COMPUTE_BACKEND` unset → `auto` → use Rust when dylib admitted, else Python MTF.**

This is **not** a full-system language rewrite. Authority surfaces stay Python forever.

---

## 1. What was delivered

### 1.1 Documents

| Path | Role |
|---|---|
| `docs/adr/ADR-0001-python-authority-rust-compute.md` | Binding ADR |
| `docs/rust_sol.md` | **This handoff** — scope contract for independent review |

### 1.2 Python platform seam

| Path | Role |
|---|---|
| `src/futures_research/platform/native_runtime.py` | Admit dylib: no symlink, size cap, re-stat, sha256, capabilities, process registry |
| `src/futures_research/platform/backend_policy.py` | `stable` / `accelerated` / `auto`; authority paths force Python; `FR_RUST_DISABLE` |
| `src/futures_research/platform/provenance.py` | `compute_provenance.v1` fields |
| `src/futures_research/platform/chart_compute_seam.py` | Product seam: resolve backend → Rust or Python → provenance; **FULL_JOB_PYTHON_FALLBACK** |
| `src/futures_research/contracts/chart_compute.py` | Wire models `chart_compute_request.v1` / `chart_compute_draft.v1` |
| `src/futures_research/backtest/chart_kernel_python.py` | **Python oracle** for chart resample + EMA |

### 1.3 Rust crate

| Path | Role |
|---|---|
| `native/fr_compute/` | `cdylib` + `rlib` |
| `native/fr_compute/fr_compute.capabilities` | `writes_authority=false`, `chart_compute_v1` |
| `native/fr_compute/src/chart.rs` | Same algorithm as Python oracle |
| `native/fr_compute/src/lib.rs` | C ABI exports |
| `native/fr_compute/src/wire.rs` | Non-finite helpers |

**Public C ABI (allowlist):**

| Symbol | Meaning |
|---|---|
| `fr_chart_compute_v1` | JSON in → JSON out (allocated) |
| `fr_string_free` | Free response buffer |
| `fr_writes_authority` | Always returns `0` |

**Build:**

```bash
cd native/fr_compute && cargo build --release
cp fr_compute.capabilities target/release/libfr_compute.dylib.capabilities
# Linux: libfr_compute.so + .capabilities
```

### 1.4 Tests (review must re-run)

| Test module | Proves |
|---|---|
| `tests/test_native_runtime_admission.py` | symlink / oversize / empty caps / sha mismatch / authority export ban |
| `tests/test_chart_compute_seam.py` | Python deterministic; stable=python; kill-switch; **Rust exact vs oracle when dylib present** |
| `tests/test_rust_authority_boundary.py` | capabilities file; no order/DB/IB markers in `.rs`; ABI allowlist |

**Re-run command:**

```bash
cd native/fr_compute && cargo build --release
cp fr_compute.capabilities target/release/libfr_compute.dylib.capabilities   # or .so
cd ../..
.venv/bin/python -m pytest tests/test_native_runtime_admission.py \
  tests/test_chart_compute_seam.py tests/test_rust_authority_boundary.py -q
```

**Implementer verification (2026-07-31, this machine):**

```text
# kernel + admission
15 passed  (native + chart seam + authority boundary)

# Phase C product E2E + chart regression cluster
35 passed  (includes test_phase_c_chart_product_e2e.py HTTP + direct)

smoke product:
  unset env (default auto) + dylib → source=backend_rust_chart_compute, effective_backend=rust
  FR_COMPUTE_BACKEND=stable → source=backend_mtf_precompute, effective_backend=python
  FR_RUST_DISABLE=1 → python MTF fallback, no 500
  GET /api/v1/system/compute-status → production_default_backend=auto, writes_authority=false
  writes_authority always false
```

---

## 2. Authority vs Compute map (project-specific)

| Concern | Owner | Notes |
|---|---|---|
| IB Gateway MD / download | **Python** | `data/ib.py`, `paper/ib_market_data.py` |
| Canonical market Parquet writes | **Python** | `data/storage.py` |
| Strategy / sketch / insight artifacts | **Python** | |
| Batch precheck / submit / queue | **Python** | `backtest_admission`, `batch_queue` |
| Immutable `runs.sqlite3` seal | **Python** | |
| PromotionDecision | **Python** | |
| Paper cash/position/fills/lifecycle | **Python** | `paper/store.py`, runtime |
| Chart resample + EMA pure kernel | **Rust optional** | via seam only |
| Full backtest engine | **Python oracle** | Rust **not** product-default; future phase |
| Web / FastAPI | **Python + React** | |

---

## 3. Runtime policy (ops)

| Env | Effect |
|---|---|
| **unset** | **`auto` (production default)** — Rust if admitted, else Python |
| `FR_COMPUTE_BACKEND=auto` | Same as unset |
| `FR_COMPUTE_BACKEND=stable` | Always Python MTF (pin / emergency) |
| `FR_COMPUTE_BACKEND=accelerated` | Prefer Rust; still falls back Python if admit/load fails |
| `FR_RUST_DISABLE=1` | Force Python |

**Provenance** on every draft:

- `schema: compute_provenance.v1`
- `requested_backend`
- `effective_backend`
- `fallback_reason`
- `artifact_sha256`
- `writes_authority: false`

**Failure policy:** On any Rust error, discard partials and run **FULL_JOB_PYTHON_FALLBACK** on the same frozen request (no mid-job stitch).

---

## 4. Explicit non-goals (do not regress)

Reviewer should **FAIL** the delivery if any of these appear:

1. Rust writes paper ledger / runs DB / promotion store  
2. Rust IB / placeOrder / network client in `fr_compute`  
3. Per-bar chatty FFI  
4. Mid-job backend switch + stitching equity  
5. Production default `accelerated` without shadow + value gate  
6. Deleting Python oracle  

---

## 5. Phase C + Phase F status — landed

| Item | Status |
|---|---|
| P5 `chart_series` materialize → Rust via seam | **Landed** when `FR_COMPUTE_BACKEND=auto\|accelerated` and dylib admitted |
| Python product fallback | **Legacy MTF** (`backend_mtf_precompute` / native daily) on any Rust failure or when mode=`stable` |
| Sidecar field `compute_provenance` | **Landed** on every materialize (`effective_backend`, `writes_authority=false`) |
| Product E2E (Phase C) | `tests/test_phase_c_chart_product_e2e.py` |
| **Phase F shadow compare** | **Landed** — `GET /api/v1/runs/{run_id}/chart/shadow-compare` |
| Diff engine | `platform/chart_shadow_compare.py` + `shadow_compare_run_chart` |
| Shadow E2E | `tests/test_phase_f_shadow_compare.py` |
| UI backend badge | **Landed** — ChartGrid pane + ChartViewer meta (`Rust` / `Python`) |
| UI shadow panel | **Landed** — Run detail `ChartShadowPanel` (on-demand) |
| Default env | **`auto` → prefer Rust when admitted** |
| Full backtest loop in Rust | Not started (phases D–E) |
| maturin/PyO3 | Not used — **ctypes C ABI** |

### Production ops

```bash
# default already auto — no env required if dylib is present
# pin pure Python:
export FR_COMPUTE_BACKEND=stable
# kill switch:
export FR_RUST_DISABLE=1

# readiness
curl -s http://127.0.0.1:8000/api/v1/system/compute-status | jq .
```

Response / sidecar extra fields:

- `source`: `backend_rust_chart_compute` | `backend_mtf_precompute` | …
- `compute_provenance.effective_backend`: `rust` | `python`
- `compute_provenance.writes_authority`: always `false`

### Phase F shadow compare

```bash
curl -s "http://127.0.0.1:8000/api/v1/runs/<run_id>/chart/shadow-compare?tf=5m" | jq .
```

Report schema `chart_shadow_compare.v1`:

- `verdict`: `PASS` | `DIFF` | `SKIPPED` | `INCOMPARABLE`
- `reference` = Python MTF (`force_backend=stable`)
- `candidate` = Rust path (`force_backend=accelerated`); SKIPPED if not effective rust
- `timeline` / `ohlc` / `ema` / `summary.max_abs_*`

### Product E2E re-run

```bash
.venv/bin/python -m pytest \
  tests/test_phase_c_chart_product_e2e.py \
  tests/test_phase_f_shadow_compare.py -q
```

---

## 6. How to exercise the seam (manual)

```python
from futures_research.contracts.chart_compute import ChartComputeRequest
from futures_research.platform.chart_compute_seam import chart_compute_v1

req = ChartComputeRequest.model_validate({
  "schema": "chart_compute_request.v1",
  "timeframe_minutes": 5,
  "ema_period": 3,
  "bars": [
    {"t": "2026-07-01T12:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 10},
    {"t": "2026-07-01T12:01:00Z", "o": 100.5, "h": 102, "l": 100, "c": 101, "v": 11},
  ],
  "requested_backend": "auto",
})
draft = chart_compute_v1(req)
print(draft.provenance["effective_backend"], draft.artifact_sha256, len(draft.points))
```

---

## 7. Suggested review checklist (for AI reviewer)

### Correctness

- [ ] Re-run the three test modules; all green with dylib present  
- [ ] Without dylib: stable path still green; accelerated falls back Python without crash  
- [ ] `writes_authority=false` present; `true` rejected by admission  
- [ ] Symlink dylib rejected  
- [ ] Python vs Rust point-wise equality on fixture bars (`test_rust_exact_when_dylib_present`)  

### Architecture

- [ ] No Rust code path imports/writes SQLite paper or runs  
- [ ] No IB / order / DB symbols in `native/fr_compute/**/*.rs`  
- [ ] Seam is the only product entry for chart compute  
- [ ] Default env still Python  

### Completeness vs claim

- [ ] Handoff claims match code (no overclaim of backtest engine)  
- [ ] ADR-0001 matches implementation  
- [ ] `Cargo.lock` present under `native/fr_compute/`  

### Security admission

- [ ] Oversize file rejected  
- [ ] sha256 mismatch rejected  
- [ ] Empty capabilities rejected  

---

## 8. Follow-up phases (for next executor)

1. ~~**Phase C:** chart_series product path~~ **DONE**  
2. ~~**Phase F (partial):** shadow compare + UI badge~~ **DONE**  
3. ~~**Phase E (vertical slice):** backtest_loop_v1 dual-EMA closed-bar~~ **DONE**  
4. ~~**Phase E.2:** Rust kernel product trade seal + evidence_complete + golden~~ **DONE**  
5. ~~**Phase E.3:** TrendStrategy Rust port + bit-exact dual-run golden~~ **DONE**  
   - Native: `trend_strategy.rs` + `fr_trend_strategy_*` ABI (`trend_strategy_v1`)  
   - Python: `RustTrendStrategy` dual-runs Python oracle + Rust FSM every bar; diverge → fallback Python + compute error  
   - Golden: `tests/test_backtest_strategy.py` parametrized `python`/`rust` for core pullback goldens  
   - Product: `create_trend_strategy()` in runner  
6. **Phase D:** Offline account reducer (events → state), exact vs Python  
7. **Phase F remainders:** session-aligned Rust resample to match MTF 1:1  

Each phase: RED→GREEN, separate commits, no mixed renames.

---

## 9. File index (touched / added)

```text
docs/adr/ADR-0001-python-authority-rust-compute.md
docs/rust_sol.md
src/futures_research/platform/__init__.py
src/futures_research/platform/native_runtime.py
src/futures_research/platform/backend_policy.py
src/futures_research/platform/provenance.py
src/futures_research/platform/chart_compute_seam.py
src/futures_research/platform/chart_series_backend.py
src/futures_research/platform/chart_shadow_compare.py
src/futures_research/api/chart_series.py
src/futures_research/api/routes_chart.py          # + /chart/shadow-compare
src/futures_research/contracts/chart_compute.py
src/futures_research/backtest/chart_kernel_python.py
apps/web/src/api/chartTypes.ts
apps/web/src/api/client.ts
apps/web/src/lib/results/normalContract.ts        # optional compute_provenance
apps/web/src/lib/results/types.ts                 # computeBackend on pane
apps/web/src/components/chart/ChartGrid.tsx        # backend badge
apps/web/src/components/chart/ChartViewer.tsx
apps/web/src/components/chart/ChartShadowPanel.tsx
apps/web/src/pages/RunDetailPage.tsx
apps/web/src/styles/results.css
native/fr_compute/...
tests/test_native_runtime_admission.py
tests/test_chart_compute_seam.py
tests/test_rust_authority_boundary.py
tests/test_phase_c_chart_product_e2e.py
tests/test_phase_f_shadow_compare.py
tests/test_backtest_loop_seam.py
tests/test_phase_e2_kernel_seal.py
src/futures_research/backtest/kernel_seal.py
src/futures_research/backtest/backtest_kernel_python.py
src/futures_research/platform/backtest_loop_seam.py
src/futures_research/contracts/backtest_loop.py
```

Built artifact (local, not necessarily committed):

```text
native/fr_compute/target/release/libfr_compute.dylib
native/fr_compute/target/release/libfr_compute.dylib.capabilities
```

---

## 10. Reviewer verdict template

Copy and fill:

```text
VERDICT: PASS | PASS_WITH_GAPS | FAIL
Evidence: <commands + counts>
Authority boundary: OK | BREACH
Default backend still Python: YES | NO
Exact oracle: YES | NO | SKIPPED (no dylib)
Gaps acceptable: <list>
Required corrections: <list>
```

---

## 11. How to start the review (prompt seed for next AI)

```text
You are reviewing the rust_sol delivery for futures-research.
Scope is ONLY docs/rust_sol.md + listed files. Do not expand into Phase C–F.
1) Read docs/adr/ADR-0001-python-authority-rust-compute.md and docs/rust_sol.md.
2) Re-run the three pytest modules after cargo build --release.
3) Grep native/fr_compute for IB/order/DB markers.
4) Confirm FR_COMPUTE_BACKEND default is auto (prefer Rust); authority still Python.
5) Fill the verdict template in section 10. Be adversarial on authority boundary and overclaim.
```

---

**End of rust_sol handoff.**  
Independent reviewer should re-run tests and treat this file as the scope contract.
