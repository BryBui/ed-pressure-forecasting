"""Download the public BHI emergency department and ambulance datasets."""

from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from curl_cffi import requests


DOWNLOADS_PAGE = "https://www.bhi.nsw.gov.au/downloadable-data"
DATASET_CLASSES = {
    "AMB": "box-link-ambulance",
    "ED": "box-link-emergency-department",
}
FILENAME_PATTERN = re.compile(
    r"^BHI_DATA_HQ_(AMB|ED)_Jan2010(?:Mar|Jun|Sep|Dec)\d{4}\.xlsx$"
)
WRAPPER_CLASSES = {"col-12", "box-wrapper", "d-flex"}

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "1.raw" / "bhi_data"


class DownloadLinkParser(HTMLParser):
    """Collect specific and fallback BHI download links from the page."""

    def __init__(self) -> None:
        super().__init__()
        self.specific_links: dict[str, list[str]] = {"AMB": [], "ED": []}
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

        for dataset, class_name in DATASET_CLASSES.items():
            if class_name in classes:
                self.specific_links[dataset].append(href)

        if self._wrapper_depth is not None and "box-link" in classes:
            self.wrapper_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._wrapper_depth == self._div_depth:
            self._wrapper_depth = None
        self._div_depth -= 1


def dataset_from_url(url: str) -> tuple[str, str] | None:
    """Return the dataset code and filename when *url* names a BHI workbook."""
    filename = Path(unquote(urlsplit(url).path)).name
    match = FILENAME_PATTERN.fullmatch(filename)
    if not match:
        return None
    return match.group(1), filename


def discover_dataset_urls(
    html: str, page_url: str = DOWNLOADS_PAGE
) -> tuple[dict[str, str], bool]:
    """Discover AMB and ED workbook URLs, falling back to filenames."""
    parser = DownloadLinkParser()
    parser.feed(html)

    discovered: dict[str, str] = {}
    for dataset, links in parser.specific_links.items():
        valid_links = {
            urljoin(page_url, link)
            for link in links
            if dataset_from_url(urljoin(page_url, link))
            and dataset_from_url(urljoin(page_url, link))[0] == dataset
        }
        if len(valid_links) == 1:
            discovered[dataset] = valid_links.pop()

    used_fallback = len(discovered) != len(DATASET_CLASSES)
    if used_fallback:
        fallback_links: dict[str, set[str]] = {"AMB": set(), "ED": set()}
        for link in parser.wrapper_links:
            url = urljoin(page_url, link)
            if dataset_details := dataset_from_url(url):
                fallback_links[dataset_details[0]].add(url)

        for dataset in DATASET_CLASSES:
            if dataset not in discovered and len(fallback_links[dataset]) == 1:
                discovered[dataset] = fallback_links[dataset].pop()

    missing = DATASET_CLASSES.keys() - discovered.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(f"Could not find a unique BHI download link for: {names}")

    return discovered, used_fallback


def fetch_dataset_urls() -> tuple[dict[str, str], bool]:
    """Fetch the BHI downloads page and discover its current workbook links."""
    response = requests.get(DOWNLOADS_PAGE, impersonate="chrome", timeout=60)
    try:
        response.raise_for_status()
        return discover_dataset_urls(response.text)
    finally:
        response.close()


def download_file(url: str, destination: Path) -> None:
    """Download *url* to *destination*, replacing an existing copy."""
    temporary_file = destination.with_suffix(destination.suffix + ".part")

    try:
        response = requests.get(url, impersonate="chrome", stream=True, timeout=60)
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "spreadsheetml" not in content_type:
                raise RuntimeError(
                    f"Expected an Excel workbook, but BHI returned {content_type or 'no content type'}"
                )

            with temporary_file.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    output.write(chunk)
        finally:
            response.close()
        temporary_file.replace(destination)
    except Exception:
        temporary_file.unlink(missing_ok=True)
        raise


def download_latest_datasets(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Discover, download and return the current AMB and ED workbooks."""
    output_dir.mkdir(parents=True, exist_ok=True)
    datasets, used_fallback = fetch_dataset_urls()
    method = "filename fallback" if used_fallback else "dataset-specific classes"
    print(f"Discovered current workbook links using {method}.")

    downloaded: list[Path] = []
    for dataset in ("AMB", "ED"):
        url = datasets[dataset]
        details = dataset_from_url(url)
        if details is None:  # Protected by discovery validation.
            raise RuntimeError(f"Invalid workbook URL: {url}")
        filename = details[1]
        destination = output_dir / filename
        print(f"Found {dataset}: {url}")
        print(f"Downloading {filename}...")
        download_file(url, destination)
        print(f"Saved to {destination}")
        downloaded.append(destination)
    return downloaded


def main() -> None:
    """Discover and download the latest BHI datasets."""
    download_latest_datasets()


if __name__ == "__main__":
    main()
