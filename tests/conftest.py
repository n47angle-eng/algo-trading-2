"""Shared fixtures for the WO-001 data-layer test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from futures_research.data.contracts import ContractRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def contracts_registry() -> ContractRegistry:
    """Load the checked-in owner-editable individual-contract metadata."""
    return ContractRegistry.from_yaml(PROJECT_ROOT / "config" / "contracts.yaml")
