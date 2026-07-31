from __future__ import annotations

from datetime import UTC, datetime

import pytest

from futures_research.paper.timeframes import (
    DEFAULT_TIMEFRAME_CAPABILITIES,
    TimeframeCapabilityError,
    TimeframeRegistry,
    load_locked_strategy_timeframe_profile,
)

STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"


def test_default_registry_keeps_runtime_roles_separate() -> None:
    registry = DEFAULT_TIMEFRAME_CAPABILITIES

    assert registry.enabled("market_input") == ("1m",)
    assert registry.enabled("execution") == ("1m",)
    assert registry.enabled("chart_display") == ("1m", "30m")
    assert registry.require("chart_display", "30m").duration_seconds == 1_800

    with pytest.raises(TimeframeCapabilityError, match="unsupported_timeframe"):
        registry.require("execution", "30m")


def test_registry_can_inject_future_timeframe_without_changing_normal_profile() -> None:
    registry = TimeframeRegistry.from_role_values(
        market_input=("1m", "5m"),
        execution=("1m", "5m"),
        chart_display=("1m", "5m", "30m"),
    )

    assert registry.require("market_input", "5m").duration_seconds == 300
    assert DEFAULT_TIMEFRAME_CAPABILITIES.enabled("market_input") == ("1m",)


def test_strategy_0003_profile_is_immutable_d_1h_5m() -> None:
    profile = load_locked_strategy_timeframe_profile(
        strategy_id="strategy-0003",
        content_sha256=STRATEGY_SHA,
    )

    assert profile.bias == "D"
    assert profile.mid == "1H"
    assert profile.entry == "5m"
    assert profile.source == "strategy.v1"
    assert profile.client_override is False


def test_capabilities_response_uses_backend_registry_and_canonical_time() -> None:
    payload = DEFAULT_TIMEFRAME_CAPABILITIES.public_capabilities(
        as_of=datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC)
    )

    assert payload["schema"] == "paper_runtime_capabilities.v1"
    assert payload["timeframes"]["chart_display"]["enabled"] == ["1m", "30m"]
    assert payload["timeframes"]["strategy_profile"] == {
        "source": "strategy.v1",
        "client_override": False,
    }
    assert payload["as_of"] == "2026-07-31T01:02:03Z"
