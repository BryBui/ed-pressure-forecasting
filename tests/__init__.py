"""Shared fixtures for reference-data tests."""

from pathlib import Path

from openpyxl import Workbook


LHDS = [
    ("CCLHD", "Central Coast Local Health District", "Central Coast", "X770"),
    ("FWLHD", "Far West Local Health District", "Far West", "X860"),
    ("HNELHD", "Hunter New England Local Health District", "Hunter New England", "X800"),
    ("ISLHD", "Illawarra Shoalhaven Local Health District", "Illawarra Shoalhaven", "X730"),
    ("MLHD", "Murrumbidgee Local Health District", "Murrumbidgee", "X840"),
    ("MNCLHD", "Mid North Coast Local Health District", "Mid North Coast", "X820"),
    ("NBMLHD", "Nepean Blue Mountains Local Health District", "Nepean Blue Mountains", "X750"),
    ("NNSWLHD", "Northern NSW Local Health District", "Northern NSW", "X810"),
    ("NSLHD", "Northern Sydney Local Health District", "Northern Sydney", "X760"),
    ("SESLHD", "South Eastern Sydney Local Health District", "South Eastern Sydney", "X720"),
    ("SNSWLHD", "Southern NSW Local Health District", "Southern NSW", "X830"),
    ("SWSLHD", "South Western Sydney Local Health District", "South Western Sydney", "X710"),
    ("SYDLHD", "Sydney Local Health District", "Sydney", "X700"),
    ("WNSWLHD", "Western NSW Local Health District", "Western NSW", "X850"),
    ("WSLHD", "Western Sydney Local Health District", "Western Sydney", "X740"),
]


def make_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Overall results"
    sheet.append(
        [
            "reporting_period",
            "reporting_level",
            "entity_id",
            "entity_longname",
            "entity_shortname",
            "lhd_shortname",
        ]
    )
    for lhd_id, name, short_name, _ in LHDS:
        sheet.append(["Apr-Jun 2026", "LHD", lhd_id, name, short_name, short_name])
    sheet.append(
        [
            "Apr-Jun 2026",
            "LHD",
            "SCHN",
            "Sydney Children's Hospitals Network",
            "Sydney Children's Hospitals Network",
            "Sydney Children's Hospitals Network",
        ]
    )
    sheet.append(
        [
            "Apr-Jun 2026",
            "LHD",
            "SVHN",
            "St Vincent's Health Network",
            "St Vincent's Health Network",
            "St Vincent's Health Network",
        ]
    )
    hospital_rows = [
        ("Jan-Mar 2026", "H001", "Coastal Hospital", "Central Coast"),
        ("Apr-Jun 2026", "H001", "Coastal Hospital", "Central Coast"),
        (
            "Apr-Jun 2026",
            "H002",
            "Children's Network Hospital",
            "Sydney Children's Hospitals Network",
        ),
        ("Jan-Mar 2026", "H003", "Historical Hospital", "Western NSW"),
    ]
    for period, hospital_id, name, parent in hospital_rows:
        sheet.append([period, "Hospital", hospital_id, name, name, parent])
    workbook.save(path)


def boundary_geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"lhd_code": code, "lhd_name": name},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[150.0, -34.0], [151.0, -34.0], [150.0, -34.0]]
                    ],
                },
            }
            for _, name, _, code in LHDS
        ],
    }


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else boundary_geojson()

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def close(self):
        return None
