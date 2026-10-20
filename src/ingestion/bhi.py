"""Discover and ingest the latest BHI emergency-department workbook."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from .storage import build_snapshot_path


DOWNLOADS_PAGE = "https://www.bhi.nsw.gov.au/downloadable-data"
ED_LINK_CLASS = "box-link-emergency-department"
WRAPPER_CLASSES = {"col-12", "box-wrapper", "d-flex"}
WORKBOOK_PATTERN = re.compile(
    r"^BHI_DATA_HQ_ED_Jan2010(?P<month>Mar|Jun|Sep|Dec)(?P<year>\d{4})\.xlsx$"
)
QUARTER_BY_END_MONTH = {"Mar": 1, "Jun": 2, "Sep": 3, "Dec": 4}
XLSX_CONTENT_TYPES = {
    "application/octet-stream",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class DownloadLinkParser(HTMLParser):
    """Collect the explicit ED link and legacy wrapper fallback links."""

    def __init__(self) -> None:
        super().__init__()
        self.ed_links: list[str] = []
        self.wrapper_links: list[str] = []
        self._div_depth = 0
        self._wrapper_depth: int | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "div":
            self._div_depth += 1
            if self._wrapper_depth is None and WRAPPER_CLASSES <= classes:
                self._wrapper_depth = self._div_depth

        if tag != "a" or not (href := attributes.get("href")):
            return
        if ED_LINK_CLASS in classes:
            self.ed_links.append(href)
        if self._wrapper_depth is not None and "box-link" in classes:
            self.wrapper_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._wrapper_depth == self._div_depth:
            self._wrapper_depth = None
        self._div_depth -= 1


@dataclass(frozen=True)
class BhiWorkbook:
    """Publication details encoded in a BHI workbook URL."""

    url: str
    source_filename: str
    year: int
    quarter: int

    @property
    def reporting_period(self) -> str:
        return f"{self.year}Q{self.quarter}"


@dataclass(frozen=True)
class IngestionResult:
    """Serializable outcome returned to Airflow."""

    status: str
    path: str
    source_url: str
    sha256: str
    reporting_period: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def workbook_from_url(url: str) -> BhiWorkbook | None:
    """Parse the advertised reporting quarter from a BHI ED workbook URL."""
    filename = Path(unquote(urlsplit(url).path)).name
    match = WORKBOOK_PATTERN.fullmatch(filename)
    if not match:
        return None
    year = int(match.group("year"))
    return BhiWorkbook(
        url=url,
        source_filename=filename,
        year=year,
        quarter=QUARTER_BY_END_MONTH[match.group("month")],
    )


def discover_ed_workbook(
    html: str, page_url: str = DOWNLOADS_PAGE
) -> BhiWorkbook:
    """Discover one current ED workbook, retaining the legacy fallback."""
    parser = DownloadLinkParser()
    parser.feed(html)

    def valid_workbooks(links: list[str]) -> dict[str, BhiWorkbook]:
        workbooks: dict[str, BhiWorkbook] = {}
        for link in links:
            url = urljoin(page_url, link)
            if workbook := workbook_from_url(url):
                workbooks[url] = workbook
        return workbooks

    candidates = valid_workbooks(parser.ed_links)
    if len(candidates) != 1:
        candidates = valid_workbooks(parser.wrapper_links)
    if len(candidates) != 1:
        raise RuntimeError(
            "Could not find a unique BHI emergency-department workbook link"
        )
    return next(iter(candidates.values()))


def _default_get(url: str, **kwargs: Any) -> Any:
    try:
        from curl_cffi import requests
    except ImportError as error:
        raise RuntimeError(
            "curl-cffi is required for live BHI downloads; install the project "
            "dependencies first"
        ) from error
    return requests.get(url, impersonate="chrome", **kwargs)


def fetch_current_workbook(
    get: Callable[..., Any] = _default_get,
) -> BhiWorkbook:
    """Fetch BHI's downloads page and return its current ED publication."""
    response = get(DOWNLOADS_PAGE, timeout=60)
    try:
        response.raise_for_status()
        return discover_ed_workbook(response.text)
    finally:
        response.close()


def _response_chunks(response: Any) -> Iterator[bytes]:
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if chunk:
            yield chunk


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matching_snapshot(directory: Path, checksum: str) -> Path | None:
    for candidate in sorted(directory.glob("ed_performance_*.xlsx")):
        if _sha256_file(candidate) == checksum:
            return candidate
    return None


def _download_to_temporary_file(
    workbook: BhiWorkbook,
    directory: Path,
    get: Callable[..., Any],
) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = NamedTemporaryFile(
        mode="wb", suffix=".xlsx.part", prefix="bhi_", dir=directory, delete=False
    )
    temporary_path = Path(temporary.name)
    digest = sha256()
    response = None
    try:
        response = get(workbook.url, stream=True, timeout=60)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in XLSX_CONTENT_TYPES:
            raise RuntimeError(
                "Expected a BHI Excel workbook, but received "
                f"{content_type or 'no content type'}"
            )
        for chunk in _response_chunks(response):
            temporary.write(chunk)
            digest.update(chunk)
        temporary.close()
        with temporary_path.open("rb") as downloaded:
            if downloaded.read(2) != b"PK":
                raise RuntimeError("BHI response is not a valid XLSX/ZIP payload")
        return temporary_path, digest.hexdigest()
    except Exception:
        temporary.close()
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()


def ingest_latest_ed(
    raw_root: Path,
    ingested_at: datetime,
    *,
    get: Callable[..., Any] = _default_get,
) -> IngestionResult:
    """Ingest the current ED workbook once, preserving revised publications."""
    workbook = fetch_current_workbook(get)
    destination = build_snapshot_path(
        raw_root,
        source="bhi",
        dataset="ed_performance",
        partitions={"year": str(workbook.year), "quarter": f"Q{workbook.quarter}"},
        reporting_period=workbook.reporting_period,
        ingested_at=ingested_at,
        extension="xlsx",
    )
    temporary_path, checksum = _download_to_temporary_file(
        workbook, destination.parent, get
    )
    try:
        if existing := _matching_snapshot(destination.parent, checksum):
            return IngestionResult(
                status="unchanged",
                path=str(existing),
                source_url=workbook.url,
                sha256=checksum,
                reporting_period=workbook.reporting_period,
            )

        try:
            # A hard link publishes the fully downloaded file atomically and refuses
            # to overwrite an existing snapshot with the same run timestamp.
            os.link(temporary_path, destination)
        except FileExistsError:
            if _sha256_file(destination) != checksum:
                raise RuntimeError(
                    f"Snapshot path already exists with different content: {destination}"
                )
            status = "unchanged"
        else:
            status = "created"

        return IngestionResult(
            status=status,
            path=str(destination),
            source_url=workbook.url,
            sha256=checksum,
            reporting_period=workbook.reporting_period,
        )
    finally:
        temporary_path.unlink(missing_ok=True)
