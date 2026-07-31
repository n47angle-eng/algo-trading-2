"""Native library admission fail-closed guards."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from futures_research.platform.native_runtime import (
    NativeAdmissionError,
    admit_native_library,
    assert_no_authority_exports,
    clear_native_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    clear_native_registry()
    yield
    clear_native_registry()


def test_reject_symlink(tmp_path: Path) -> None:
    real = tmp_path / "lib.so"
    real.write_bytes(b"\x00" * 64)
    link = tmp_path / "lib_link.so"
    link.symlink_to(real)
    with pytest.raises(NativeAdmissionError, match="symlink"):
        admit_native_library(
            link,
            module_key="t",
            required_capabilities=("chart_compute_v1", "writes_authority=false"),
        )


def test_reject_oversize(tmp_path: Path) -> None:
    blob = tmp_path / "big.so"
    blob.write_bytes(b"\x00" * 100)
    (tmp_path / "big.so.capabilities").write_text(
        "writes_authority=false\nchart_compute_v1\n", encoding="utf-8"
    )
    with pytest.raises(NativeAdmissionError, match="size cap"):
        admit_native_library(
            blob,
            module_key="t",
            max_bytes=50,
            required_capabilities=("chart_compute_v1", "writes_authority=false"),
        )


def test_reject_empty_capabilities(tmp_path: Path) -> None:
    blob = tmp_path / "lib.so"
    blob.write_bytes(b"\x00" * 32)
    with pytest.raises(NativeAdmissionError, match="capability"):
        admit_native_library(blob, module_key="t", required_capabilities=())


def test_reject_writes_authority_true(tmp_path: Path) -> None:
    blob = tmp_path / "lib.so"
    blob.write_bytes(b"\x00" * 32)
    (tmp_path / "lib.so.capabilities").write_text(
        "writes_authority=true\nchart_compute_v1\n", encoding="utf-8"
    )
    with pytest.raises(NativeAdmissionError, match="forbidden|writes_authority"):
        admit_native_library(
            blob,
            module_key="t",
            required_capabilities=("chart_compute_v1",),
        )


def test_assert_no_authority_exports() -> None:
    assert_no_authority_exports(["fr_chart_compute_v1", "fr_string_free"])
    with pytest.raises(NativeAdmissionError):
        assert_no_authority_exports(["broker_placeOrder"])


def test_sha256_mismatch(tmp_path: Path) -> None:
    blob = tmp_path / "lib.so"
    blob.write_bytes(b"\x00" * 32)
    (tmp_path / "lib.so.capabilities").write_text(
        "writes_authority=false\nchart_compute_v1\n", encoding="utf-8"
    )
    with pytest.raises(NativeAdmissionError, match="sha256"):
        admit_native_library(
            blob,
            module_key="t",
            expected_sha256="0" * 64,
            required_capabilities=("chart_compute_v1", "writes_authority=false"),
        )
