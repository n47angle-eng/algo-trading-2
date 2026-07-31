"""Static boundary: Rust sources must not grow authority surfaces."""

from __future__ import annotations

from pathlib import Path

from futures_research.paths import PROJECT_ROOT
from futures_research.platform.native_runtime import assert_no_authority_exports

ROOT = PROJECT_ROOT / "native" / "fr_compute"


def test_capabilities_file_declares_writes_authority_false() -> None:
    text = (ROOT / "fr_compute.capabilities").read_text(encoding="utf-8")
    assert "writes_authority=false" in text
    assert "writes_authority=true" not in text


def test_rust_sources_have_no_order_or_db_markers() -> None:
    forbidden = (
        "placeOrder",
        "cancelOrder",
        "sqlite",
        "reqMktData",  # IB market subscribe stays Python; compute crate must not
        "postgres",
        "sqlx",
        "tokio::net",
    )
    for path in ROOT.rglob("*.rs"):
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for marker in forbidden:
            assert marker.lower() not in lower, f"{path} contains {marker}"


def test_public_c_abi_symbols_are_compute_only() -> None:
    # Documented public ABI — if expanded, admission tests must update.
    allowed = {
        "fr_chart_compute_v1",
        "fr_backtest_loop_v1",
        "fr_trend_strategy_new",
        "fr_trend_strategy_on_entry_bar",
        "fr_trend_strategy_end_day",
        "fr_trend_strategy_state",
        "fr_string_free",
        "fr_writes_authority",
    }
    assert_no_authority_exports(list(allowed))
