# ADR-0001: Python Authority + Rust Compute

Status: **Accepted for implementation** (Owner directed 2026-07-31)  
Scope: futures-research personal research platform  
Related handoff: `docs/rust_sol.md`

## Context

The platform needs faster pure numeric work (chart resample, later MTF / offline backtest drafts) without moving trading authority out of Python. IB, paper ledger, run seal, and promotion must remain auditable single-owner surfaces.

## Decision

- **Python** owns all authority: IB MD, market writes, strategy/run/paper/promotion stores, batch admission, result seal, Web API.
- **Rust** is pure compute only: chart kernels first; (future) MTF/backtest **draft** loops.
- Capability always declares `writes_authority=false`. Admission rejects `writes_authority=true`.
- Default compute backend mode is **`auto`**: prefer Rust when the native library is admitted; otherwise Python. Pin pure Python with `FR_COMPUTE_BACKEND=stable` or kill-switch `FR_RUST_DISABLE=1`.
- On Rust failure: discard partials and **FULL_JOB_PYTHON_FALLBACK** with the same frozen input.
- No mid-job backend switch; no chatty per-bar FFI; no Rust network/DB/IB.
- Integration is **ctypes C ABI** (not PyO3) so admission can hash/size-check the dylib bytes.

## First vertical slice (in scope)

`chart_compute_v1`: pure OHLCV resample + EMA on frozen bars → draft series.

- Python oracle: `backtest/chart_kernel_python.py`
- Seam: `platform/chart_compute_seam.py`
- Rust: `native/fr_compute` (`fr_chart_compute_v1`)

Python validates finite + schema. **Phase C:** product chart materialize (`api/chart_series.py`) prefers Rust via `platform/chart_series_backend.py` under production default `auto` (or explicit `accelerated`) when the dylib is admitted; on failure it full-job falls back to legacy Python MTF. **Authority paths (IB, paper, seal, promotion) never select Rust.**

## Consequences

- Production behavior unchanged unless operators opt in.
- Reviewers must check authority boundary and non-goals in `docs/rust_sol.md`.
- Further phases (chart_series wire, account reducer, backtest draft) need separate RED→GREEN work and must not flip default to accelerated without a value gate.

## Non-goals

Rust paper ledger, IB transport, promotion decisions, immutable run publication, OMS/order placement.
