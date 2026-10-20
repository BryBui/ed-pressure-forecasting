"""Download immutable official ABS SA1 GDA2020 shapefile ZIPs by Census year.

Run: .venv-wsl/bin/python -m ingestion.SA1_shapefile --census-year 2021
The archive is retained intact; NSW filtering belongs in reference processing.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import BadZipFile, ZipFile

from .seifa import (
    IngestionResult, ReleaseLinkParser, _default_get, _require_official_abs_url,
    _sha256_file,
)
from .storage import build_snapshot_path
from urllib.parse import unquote, urljoin, urlsplit


BOUNDARY_ROOT = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs"
)


def boundary_page(census_year: int) -> str:
    """Use the ASGS edition matching the SEIFA year, never the latest geography."""
    if census_year < 2021 or (census_year - 2021) % 5:
        raise ValueError("Expected a Census year from 2021 in five-year intervals")
    edition = 3 + (census_year - 2021) // 5
    return (
        f"{BOUNDARY_ROOT}/edition-{edition}-july-{census_year}"
        f"-june-{census_year + 5}/access-and-downloads/digital-boundary-files"
    )


def discover_shapefile(html: str, page_url: str, census_year: int) -> str:
    """Select only the national SA1 GDA2020 shapefile for the requested year."""
    parser = ReleaseLinkParser()
    parser.feed(html)
    filename = f"SA1_{census_year}_AUST_SHP_GDA2020.zip"
    candidates = set()
    for link in parser.links:
        url = urljoin(page_url, link)
        try:
            _require_official_abs_url(url, "boundary download URL")
        except ValueError:
            continue
        if Path(unquote(urlsplit(url).path)).name.lower() == filename.lower():
            candidates.add(url)
    if len(candidates) != 1:
        raise RuntimeError(f"Could not find a unique ABS {filename} download")
    return candidates.pop()


def validate_shapefile(path: Path, census_year: int) -> None:
    """Check ZIP integrity and the required same-stem shapefile components."""
    stem = f"SA1_{census_year}_AUST_GDA2020".lower()
    try:
        with ZipFile(path) as archive:
            names = {Path(name).name.lower() for name in archive.namelist()}
            required = {stem + suffix for suffix in (".shp", ".shx", ".dbf", ".prj")}
            if not required.issubset(names):
                raise RuntimeError("ABS ZIP is missing the expected SA1 shapefile components")
            if archive.testzip() is not None:
                raise RuntimeError("ABS shapefile ZIP failed its integrity check")
    except BadZipFile as error:
        raise RuntimeError("ABS response is not a readable shapefile ZIP") from error


def ingest_sa1_shapefile(
    raw_root: Path,
    ingested_at: datetime,
    census_year: int,
    *,
    get: Any = _default_get,
) -> IngestionResult:
    """Fetch missing boundaries; reuse a validated existing year without network I/O."""
    page_url = boundary_page(census_year)
    destination = build_snapshot_path(
        raw_root, source="abs", dataset="SA1",
        partitions={"census_year": str(census_year)},
        reporting_period=str(census_year), ingested_at=ingested_at, extension="zip",
    )
    for existing in sorted(destination.parent.glob(f"SA1_{census_year}_*.zip")):
        if existing.name.endswith(".part.zip"):
            continue
        validate_shapefile(existing, census_year)
        return IngestionResult(
            "unchanged", str(existing), page_url, _sha256_file(existing), str(census_year)
        )

    response = get(page_url, timeout=60)
    try:
        response.raise_for_status()
        source_url = discover_shapefile(response.text, page_url, census_year)
    finally:
        response.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=destination.parent, prefix="SA1_", suffix=".part.zip", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        response = get(source_url, stream=True, timeout=300)
        try:
            response.raise_for_status()
            with temporary_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        finally:
            response.close()
        validate_shapefile(temporary_path, census_year)
        checksum = _sha256_file(temporary_path)
        try:
            os.link(temporary_path, destination)
            status = "created"
        except FileExistsError:
            if _sha256_file(destination) != checksum:
                raise RuntimeError(f"Snapshot already exists with different content: {destination}")
            status = "unchanged"
        return IngestionResult(
            status, str(destination), source_url, checksum, str(census_year)
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census-year", type=int, required=True)
    parser.add_argument(
        "--raw-root", type=Path,
        default=Path(__file__).resolve().parents[2] / "data/raw",
    )
    args = parser.parse_args()
    result = ingest_sa1_shapefile(
        args.raw_root, datetime.now(timezone.utc), args.census_year
    )
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
