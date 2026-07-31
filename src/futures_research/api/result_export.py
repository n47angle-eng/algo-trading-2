"""In-memory packaging for one immutable, integrity-checked result.v1 bundle."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from futures_research.api.results_catalog import ResultsCatalog


def build_result_export(catalog: ResultsCatalog, run_id: str) -> bytes:
    """Build all four members before a response exists, so failure cannot leak a partial ZIP."""
    artifacts = catalog.get_export_artifacts(run_id)
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_STORED, strict_timestamps=True) as archive:
        for member_name, payload in artifacts.members:
            info = ZipInfo(member_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return output.getvalue()
