from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from futures_research.api.paper_runtime import (
    PaperRuntimeCapabilities,
    PaperTraderCreateRequestV2,
    PaperTraderSelectionV2,
    build_runtime_capabilities,
    selection_fingerprint,
)
from futures_research.paper.timeframes import DEFAULT_TIMEFRAME_CAPABILITIES

STRATEGY_SHA = "4a115de8f11b18d6fa94eb95eadbbf2451d48ef1ad1d2586399e09cfa4a24b97"
BASELINE_SHA = "5edc3cf2e5d314ddc2594f57cfbc52987e35b855d804c147f86d7e6e3cf486f7"
REQUEST_ID = "4322a78f-7603-4778-bf34-a1f6369d772c"


def selection_payload() -> dict[str, object]:
    return {
        "strategy_id": "strategy-0003",
        "content_sha256": STRATEGY_SHA,
        "contract_id": "NQ-202609-CME",
        "baseline_run_id": "nq-20260728-standard-365adf",
        "baseline_result_sha256": BASELINE_SHA,
        "timeframes": {
            "market_input": "1m",
            "execution": "1m",
            "chart_display": "30m",
        },
    }


def test_v2_create_contract_is_exact_and_keeps_all_timeframe_roles() -> None:
    model = PaperTraderCreateRequestV2.model_validate(
        {
            "schema": "paper_trader_create_request.v2",
            "request_id": REQUEST_ID,
            "selection": selection_payload(),
        }
    )

    assert UUID(model.request_id).version == 4
    assert model.selection.timeframes.market_input == "1m"
    assert model.selection.timeframes.execution == "1m"
    assert model.selection.timeframes.chart_display == "30m"
    assert model.model_dump(by_alias=True, mode="json")["schema"] == (
        "paper_trader_create_request.v2"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": "paper_trader_create_request.v2"},
        {"unexpected": True},
        {"schema": "paper_trader_create_request.v1"},
    ],
)
def test_v2_create_rejects_alias_extra_and_wrong_schema(
    patch: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "schema": "paper_trader_create_request.v2",
        "request_id": REQUEST_ID,
        "selection": selection_payload(),
    }
    if "schema_version" in patch:
        payload.pop("schema")
    payload.update(patch)

    with pytest.raises(ValidationError):
        PaperTraderCreateRequestV2.model_validate(payload)


def test_selection_fingerprint_is_stable_and_timeframe_sensitive() -> None:
    base = PaperTraderSelectionV2.model_validate(selection_payload())
    changed_payload = selection_payload()
    changed_payload["timeframes"] = {
        "market_input": "1m",
        "execution": "1m",
        "chart_display": "1m",
    }
    changed = PaperTraderSelectionV2.model_validate(changed_payload)

    assert selection_fingerprint(base) == selection_fingerprint(base)
    assert selection_fingerprint(base) != selection_fingerprint(changed)


def test_capabilities_model_is_strict_and_ordered() -> None:
    response = build_runtime_capabilities(
        DEFAULT_TIMEFRAME_CAPABILITIES,
        as_of=datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC),
    )

    assert isinstance(response, PaperRuntimeCapabilities)
    assert response.market_modes == ("live", "test_delayed")
    assert response.lifecycle_states[-1] == "permanently_stopped"
    assert response.safety_defaults.max_drawdown_r == 8
    assert response.model_dump(by_alias=True, mode="json")["as_of"] == (
        "2026-07-31T01:02:03Z"
    )
