"""P6 one-off provisioning authorization policy and wire contract tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from futures_research.api.deps import (
    get_paper_provisioning_authorization_policy,
)
from futures_research.api.paper_provisioning import (
    DisabledPaperProvisioningAuthorizationPolicy,
    OneOffPaperProvisioningAuthorizationPolicy,
    PaperProvisioningAuthorization,
    PaperProvisioningClaim,
    PaperProvisioningNotAuthorizedError,
    PaperProvisioningReadiness,
    PaperProvisioningReadinessRequest,
    PaperProvisioningRequestConflictError,
    PaperProvisioningSelectionMismatchError,
)
from futures_research.api.paper_traders import (
    PaperReadiness,
    PaperReadinessCheck,
    PaperRequestConflictError,
    PaperTraderSelection,
)

_AUTHORIZED_AT = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
_OPERATION_ID = "paper-provision-" + ("1" * 32)
_REQUEST_ID = "00000000-0000-4000-8000-000000000001"
_OTHER_REQUEST_ID = "00000000-0000-4000-8000-000000000002"
_PAYLOAD_SHA = "a" * 64
_OTHER_PAYLOAD_SHA = "b" * 64
_REASON = "Owner-approved one-off P6 provision-only flow verification"


@dataclass
class _Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current


def _selection(**updates: str) -> PaperTraderSelection:
    values = {
        "strategy_id": "strategy-0003",
        "content_sha256": "c" * 64,
        "contract_id": "NQ-202609-CME",
        "baseline_run_id": "nq-20260728-standard-365adf",
        "baseline_result_sha256": "d" * 64,
    }
    values.update(updates)
    return PaperTraderSelection.model_validate(values, strict=True)


def _policy(
    clock: _Clock,
    *,
    selection: PaperTraderSelection | None = None,
) -> OneOffPaperProvisioningAuthorizationPolicy:
    return OneOffPaperProvisioningAuthorizationPolicy(
        operation_id=_OPERATION_ID,
        authorized_selection=selection or _selection(),
        authorized_at=_AUTHORIZED_AT,
        clock=clock,
    )


def _claim(
    policy: OneOffPaperProvisioningAuthorizationPolicy,
    *,
    request_id: str = _REQUEST_ID,
    request_payload_sha256: str = _PAYLOAD_SHA,
    selection: PaperTraderSelection | None = None,
) -> PaperProvisioningAuthorization:
    return policy.claim(
        request_id=request_id,
        request_payload_sha256=request_payload_sha256,
        selection=selection or _selection(),
    )


def test_provisioning_wire_models_accept_only_schema_alias() -> None:
    request = PaperProvisioningReadinessRequest.model_validate(
        {
            "schema": "paper_provisioning_readiness_request.v1",
            "selection": _selection().model_dump(mode="json"),
        },
        strict=True,
    )
    assert request.selection == _selection()

    claim = PaperProvisioningClaim.model_validate(
        {
            "request_id": _REQUEST_ID,
            "request_payload_sha256": _PAYLOAD_SHA,
            "selection": _selection().model_dump(mode="json"),
            "claimed_at": "2026-07-30T09:01:00Z",
        },
        strict=True,
    )
    assert claim.request_id == _REQUEST_ID

    authorization = PaperProvisioningAuthorization.model_validate(
        {
            "schema": "paper_provisioning_authorization.v1",
            "state": "armed",
            "operation_id": _OPERATION_ID,
            "authorized_at": "2026-07-30T09:00:00Z",
            "expires_at": "2026-07-30T09:30:00Z",
            "reason": _REASON,
        },
        strict=True,
    )
    assert authorization.expires_at == "2026-07-30T09:30:00Z"

    checked_at = "2026-07-30T09:01:00Z"
    runtime = PaperReadiness(
        schema="paper_readiness.v1",
        selection=_selection(),
        overall="blocked",
        market_session="unknown",
        checked_at=checked_at,
        checks=tuple(
            PaperReadinessCheck(
                key=key,
                status="blocked" if key == "baseline_integrity" else "unknown",
                reason="isolated structural truth",
                checked_at=checked_at,
            )
            for key in (
                "ib_realtime",
                "exchange_calendar",
                "telegram",
                "baseline_integrity",
            )
        ),
    )
    readiness = PaperProvisioningReadiness(
        schema="paper_provisioning_readiness.v1",
        selection=_selection(),
        can_provision=False,
        authorization=authorization,
        runtime_readiness=runtime,
    )

    canonical_models = (
        (
            PaperProvisioningReadinessRequest,
            request.model_dump(by_alias=True, mode="python"),
        ),
        (
            PaperProvisioningAuthorization,
            authorization.model_dump(by_alias=True, mode="python"),
        ),
        (
            PaperProvisioningReadiness,
            readiness.model_dump(by_alias=True, mode="python"),
        ),
    )
    for model, canonical in canonical_models:
        assert (
            model.model_validate(canonical, strict=True).model_dump(
                by_alias=True,
                mode="python",
            )
            == canonical
        )

        schema_version_only = dict(canonical)
        schema_version_only["schema_version"] = schema_version_only.pop("schema")
        with pytest.raises(ValidationError):
            model.model_validate(schema_version_only, strict=True)

        both_names = {
            **canonical,
            "schema_version": canonical["schema"],
        }
        with pytest.raises(ValidationError):
            model.model_validate(both_names, strict=True)

        missing_schema = dict(canonical)
        missing_schema.pop("schema")
        with pytest.raises(ValidationError):
            model.model_validate(missing_schema, strict=True)

        wrong_schema = {
            **canonical,
            "schema": f"{canonical['schema']}.wrong",
        }
        with pytest.raises(ValidationError):
            model.model_validate(wrong_schema, strict=True)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema": "paper_provisioning_readiness_request.v1",
            "selection": None,
        },
        {
            "schema": "paper_provisioning_readiness_request.v1",
        },
        {
            "schema": "paper_provisioning_readiness_request.v1",
            "selection": {
                **_selection().model_dump(mode="json"),
                "fallback": True,
            },
        },
        {
            "schema": "paper_provisioning_readiness_request.v2",
            "selection": _selection().model_dump(mode="json"),
        },
        {
            "schema": "paper_provisioning_readiness_request.v1",
            "selection": _selection().model_dump(mode="json"),
            "request_id": _REQUEST_ID,
        },
    ],
)
def test_provisioning_readiness_request_rejects_shape_drift(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PaperProvisioningReadinessRequest.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "updates",
    [
        {"state": "ready"},
        {"operation_id": "paper-provision-" + ("A" * 32)},
        {"operation_id": "paper-provision-" + ("1" * 31)},
        {"authorized_at": "2026-07-30T09:00:00+00:00"},
        {"expires_at": "2026-07-30T09:29:59Z"},
        {"reason": " "},
        {"unexpected": True},
    ],
)
def test_authorization_model_rejects_enum_identity_time_and_shape_drift(
    updates: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "schema": "paper_provisioning_authorization.v1",
        "state": "armed",
        "operation_id": _OPERATION_ID,
        "authorized_at": "2026-07-30T09:00:00Z",
        "expires_at": "2026-07-30T09:30:00Z",
        "reason": _REASON,
    }
    payload.update(updates)
    with pytest.raises(ValidationError):
        PaperProvisioningAuthorization.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "updates",
    [
        {"request_id": "00000000-0000-1000-8000-000000000001"},
        {"request_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"request_payload_sha256": "A" * 64},
        {"request_payload_sha256": "a" * 63},
        {"claimed_at": "2026-07-30T09:01:00+00:00"},
        {"selection": None},
        {"extra": "forbidden"},
    ],
)
def test_claim_model_rejects_uuid_sha_time_and_shape_drift(
    updates: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "request_id": _REQUEST_ID,
        "request_payload_sha256": _PAYLOAD_SHA,
        "selection": _selection().model_dump(mode="json"),
        "claimed_at": "2026-07-30T09:01:00Z",
    }
    payload.update(updates)
    with pytest.raises(ValidationError):
        PaperProvisioningClaim.model_validate(payload, strict=True)


def test_authorization_null_boundaries_and_exact_thirty_minutes() -> None:
    disabled = PaperProvisioningAuthorization(
        schema="paper_provisioning_authorization.v1",
        state="disabled",
        operation_id=None,
        authorized_at=None,
        expires_at=None,
        reason="paper trader provisioning is disabled in the normal runtime",
    )
    assert disabled.state == "disabled"

    for updates in (
        {"operation_id": _OPERATION_ID},
        {"authorized_at": "2026-07-30T09:00:00Z"},
        {"expires_at": "2026-07-30T09:30:00Z"},
    ):
        payload = disabled.model_dump(by_alias=True, mode="json")
        payload.update(updates)
        with pytest.raises(ValidationError):
            PaperProvisioningAuthorization.model_validate(payload, strict=True)

    for field in ("operation_id", "authorized_at", "expires_at"):
        payload = {
            "schema": "paper_provisioning_authorization.v1",
            "state": "armed",
            "operation_id": _OPERATION_ID,
            "authorized_at": "2026-07-30T09:00:00Z",
            "expires_at": "2026-07-30T09:30:00Z",
            "reason": _REASON,
        }
        payload[field] = None
        with pytest.raises(ValidationError):
            PaperProvisioningAuthorization.model_validate(payload, strict=True)


def test_disabled_policy_is_request_relative_and_leaks_no_operation() -> None:
    policy = DisabledPaperProvisioningAuthorizationPolicy()
    first = policy.authorization_for(_selection())
    second = policy.authorization_for(
        _selection(contract_id="YM-202609-CBOT")
    )
    assert first == second
    assert first.model_dump(by_alias=True, mode="json") == {
        "schema": "paper_provisioning_authorization.v1",
        "state": "disabled",
        "operation_id": None,
        "authorized_at": None,
        "expires_at": None,
        "reason": "paper trader provisioning is disabled in the normal runtime",
    }


def test_one_off_projection_hides_operation_from_wrong_selection() -> None:
    clock = _Clock(_AUTHORIZED_AT + timedelta(minutes=1))
    policy = _policy(clock)
    armed = policy.authorization_for(_selection())
    hidden = policy.authorization_for(
        _selection(contract_id="YM-202609-CBOT")
    )
    assert armed.model_dump(by_alias=True, mode="json") == {
        "schema": "paper_provisioning_authorization.v1",
        "state": "armed",
        "operation_id": _OPERATION_ID,
        "authorized_at": "2026-07-30T09:00:00Z",
        "expires_at": "2026-07-30T09:30:00Z",
        "reason": _REASON,
    }
    assert hidden.state == "disabled"
    assert hidden.operation_id is None
    assert hidden.authorized_at is None
    assert hidden.expires_at is None


def test_server_create_inspection_is_exact_nonclaiming_and_unconcealed() -> None:
    clock = _Clock(_AUTHORIZED_AT + timedelta(minutes=1))
    policy = _policy(clock)

    armed = policy.inspect_for_create(_selection())

    assert armed.authorization.state == "armed"
    assert armed.claim is None
    assert policy.authorization_for(_selection()).state == "armed"
    with pytest.raises(PaperProvisioningSelectionMismatchError):
        policy.inspect_for_create(
            _selection(contract_id="YM-202609-CBOT")
        )
    assert policy.authorization_for(
        _selection(contract_id="YM-202609-CBOT")
    ).state == "disabled"


def test_server_create_inspection_state_matrix_is_fail_closed() -> None:
    selection = _selection()
    with pytest.raises(PaperProvisioningNotAuthorizedError):
        DisabledPaperProvisioningAuthorizationPolicy().inspect_for_create(
            selection
        )

    expired = _policy(_Clock(_AUTHORIZED_AT + timedelta(minutes=30)))
    with pytest.raises(PaperProvisioningNotAuthorizedError):
        expired.inspect_for_create(selection)
    assert expired.authorization_for(selection).state == "expired"

    claimed = _policy(_Clock(_AUTHORIZED_AT + timedelta(minutes=1)))
    _claim(claimed)
    claimed_inspection = claimed.inspect_for_create(selection)
    assert claimed_inspection.authorization.state == "claimed"
    assert claimed_inspection.claim is not None
    assert claimed_inspection.claim.request_id == _REQUEST_ID
    assert claimed_inspection.claim.request_payload_sha256 == _PAYLOAD_SHA
    assert claimed_inspection.claim.claimed_at == "2026-07-30T09:01:00Z"

    claimed.mark_consumed(
        request_id=_REQUEST_ID,
        request_payload_sha256=_PAYLOAD_SHA,
        selection=selection,
    )
    consumed = claimed.inspect_for_create(selection)
    assert consumed.authorization.state == "consumed"
    assert consumed.claim == claimed_inspection.claim


def test_can_provision_requires_ready_baseline_integrity() -> None:
    selection = _selection()
    authorization = _policy(
        _Clock(_AUTHORIZED_AT + timedelta(minutes=1))
    ).authorization_for(selection)
    checked_at = "2026-07-30T09:01:00Z"
    checks = tuple(
        PaperReadinessCheck(
            key=key,
            status="blocked" if key == "baseline_integrity" else "unknown",
            reason="isolated structural truth",
            checked_at=checked_at,
        )
        for key in (
            "ib_realtime",
            "exchange_calendar",
            "telegram",
            "baseline_integrity",
        )
    )
    runtime = PaperReadiness(
        schema="paper_readiness.v1",
        selection=selection,
        overall="blocked",
        market_session="unknown",
        checked_at=checked_at,
        checks=checks,
    )
    response = PaperProvisioningReadiness(
        schema="paper_provisioning_readiness.v1",
        selection=selection,
        can_provision=False,
        authorization=authorization,
        runtime_readiness=runtime,
    )
    assert response.can_provision is False
    drifted = response.model_dump(by_alias=True, mode="json")
    drifted["can_provision"] = True
    with pytest.raises(ValidationError):
        PaperProvisioningReadiness.model_validate(
            drifted,
            strict=True,
        )


def test_claim_append_failure_and_consumption_never_rearm() -> None:
    clock = _Clock(_AUTHORIZED_AT + timedelta(minutes=1))
    policy = _policy(clock)
    assert _claim(policy).state == "claimed"
    assert (
        policy.record_append_failure(
            request_id=_REQUEST_ID,
            request_payload_sha256=_PAYLOAD_SHA,
            selection=_selection(),
        ).state
        == "claimed"
    )
    assert policy.authorization_for(_selection()).state == "claimed"
    assert (
        policy.mark_consumed(
            request_id=_REQUEST_ID,
            request_payload_sha256=_PAYLOAD_SHA,
            selection=_selection(),
        ).state
        == "consumed"
    )
    assert _claim(policy).state == "consumed"
    assert policy.authorization_for(_selection()).state == "consumed"


def test_same_binding_reenters_but_different_payload_conflicts() -> None:
    policy = _policy(_Clock(_AUTHORIZED_AT + timedelta(minutes=1)))
    assert _claim(policy).state == "claimed"
    assert _claim(policy).state == "claimed"
    with pytest.raises(PaperRequestConflictError):
        _claim(policy, request_payload_sha256=_OTHER_PAYLOAD_SHA)
    assert policy.authorization_for(_selection()).state == "claimed"


def test_two_request_race_has_exactly_one_claim_and_no_overwrite() -> None:
    policy = _policy(_Clock(_AUTHORIZED_AT + timedelta(minutes=1)))

    def attempt(request_id: str) -> tuple[str, str]:
        try:
            result = _claim(policy, request_id=request_id)
            return request_id, result.state
        except PaperProvisioningRequestConflictError:
            return request_id, "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(attempt, (_REQUEST_ID, _OTHER_REQUEST_ID))
        )

    winners = [request_id for request_id, result in results if result == "claimed"]
    losers = [request_id for request_id, result in results if result == "conflict"]
    assert len(winners) == len(losers) == 1
    assert _claim(policy, request_id=winners[0]).state == "claimed"
    with pytest.raises(PaperProvisioningRequestConflictError):
        _claim(policy, request_id=losers[0])


def test_first_claim_expiry_boundary_and_claimed_recovery_after_expiry() -> None:
    before_boundary = _Clock(
        _AUTHORIZED_AT + timedelta(minutes=30) - timedelta(microseconds=1)
    )
    claimed = _policy(before_boundary)
    assert _claim(claimed).state == "claimed"
    before_boundary.current = _AUTHORIZED_AT + timedelta(hours=1)
    assert _claim(claimed).state == "claimed"

    at_boundary = _Clock(_AUTHORIZED_AT + timedelta(minutes=30))
    expired = _policy(at_boundary)
    with pytest.raises(PaperProvisioningNotAuthorizedError):
        _claim(expired)
    assert expired.authorization_for(_selection()).state == "expired"
    at_boundary.current = _AUTHORIZED_AT + timedelta(minutes=1)
    with pytest.raises(PaperProvisioningNotAuthorizedError):
        _claim(expired)
    assert expired.authorization_for(_selection()).state == "expired"


def test_new_normal_policy_is_disabled_and_never_rearms_from_process_state() -> None:
    claimed = _policy(_Clock(_AUTHORIZED_AT + timedelta(minutes=1)))
    assert _claim(claimed).state == "claimed"

    restarted = DisabledPaperProvisioningAuthorizationPolicy()
    assert restarted.authorization_for(_selection()).state == "disabled"
    with pytest.raises(PaperProvisioningNotAuthorizedError):
        restarted.claim(
            request_id=_REQUEST_ID,
            request_payload_sha256=_PAYLOAD_SHA,
            selection=_selection(),
        )

    get_paper_provisioning_authorization_policy.cache_clear()
    default = get_paper_provisioning_authorization_policy()
    assert isinstance(default, DisabledPaperProvisioningAuthorizationPolicy)
    assert default.authorization_for(_selection()).state == "disabled"
