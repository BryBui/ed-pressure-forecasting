from pathlib import Path
import csv
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

import geopandas as gpd
from shapely.geometry import box

from reference.SA1_to_LHD import build_mapping, map_sa1_to_lhds, read_sa1_layer


class Sa1ToLhdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lhds = gpd.GeoDataFrame(
            {
                "lhd_code": ["X700", "X710"],
                "lhd_name": ["Western LHD", "Eastern LHD"],
            },
            geometry=[
                box(150.0, -34.0, 151.0, -33.0),
                box(151.0, -34.0, 152.0, -33.0),
            ],
            crs="EPSG:4326",
        )

    def test_maps_by_largest_area_overlap_and_filters_to_nsw(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {
                "SA1_CODE21": ["10101000001", "10101000002", "20101000001"],
                "STE_CODE21": ["1", "1", "2"],
            },
            geometry=[
                box(150.1, -33.9, 150.9, -33.1),
                box(150.8, -33.9, 151.6, -33.1),
                box(150.1, -33.9, 150.9, -33.1),
            ],
            crs="EPSG:4326",
        )

        rows = map_sa1_to_lhds(sa1s, self.lhds).to_dict("records")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["lhd_code"], "X700")
        self.assertEqual(rows[1]["lhd_code"], "X710")
        self.assertAlmostEqual(float(rows[0]["overlap_pct"]), 100.0, places=4)
        self.assertAlmostEqual(float(rows[1]["overlap_pct"]), 75.0, delta=0.1)
        self.assertEqual(rows[1]["assignment_method"], "largest_area_overlap")

    def test_excludes_an_unmapped_nsw_sa1_with_warning(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {"SA1_CODE_2021": ["10101000003"], "STATE_CODE_2021": ["1"]},
            geometry=[box(145.0, -34.0, 146.0, -33.0)],
            crs="EPSG:4326",
        )

        with self.assertWarnsRegex(UserWarning, "no positive-area LHD overlap: 10101000003"):
            mapping = map_sa1_to_lhds(sa1s, self.lhds)
        self.assertTrue(mapping.empty)
        self.assertEqual(
            list(mapping.columns),
            ["sa1_code_2021", "lhd_code", "lhd_name", "overlap_pct", "assignment_method"],
        )

    def test_excludes_disjoint_and_touching_areas_but_keeps_partial_overlap(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {
                "SA1_CODE21": ["10101000001", "10101000002", "10101000003"],
                "STE_CODE21": ["1", "1", "1"],
            },
            geometry=[
                box(5, 0, 15, 10),
                box(20, 0, 30, 10),
                box(10, 0, 20, 10),
            ],
            crs="EPSG:3577",
        )
        lhds = gpd.GeoDataFrame(
            {"lhd_code": ["X700"], "lhd_name": ["Western LHD"]},
            geometry=[box(0, 0, 10, 10)], crs="EPSG:3577",
        )
        with self.assertWarnsRegex(UserWarning, "10101000002, 10101000003"):
            mapping = map_sa1_to_lhds(sa1s, lhds)
        self.assertEqual(mapping["sa1_code_2021"].tolist(), ["10101000001"])
        self.assertEqual(float(mapping.iloc[0]["overlap_pct"]), 50.0)

    def test_excludes_only_known_nonspatial_records(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {"SA1_CODE21": ["10101000001", "19999949999"], "STE_CODE21": ["1", "1"]},
            geometry=[box(150.1, -33.9, 150.9, -33.1), None],
            crs="EPSG:4326",
        )
        with self.assertWarnsRegex(UserWarning, "19999949999"):
            mapping = map_sa1_to_lhds(sa1s, self.lhds)
        self.assertEqual(mapping["sa1_code_2021"].tolist(), ["10101000001"])
        sa1s.loc[1, "SA1_CODE21"] = "10101000002"
        with self.assertRaisesRegex(ValueError, "missing or empty geometry"):
            map_sa1_to_lhds(sa1s, self.lhds)

    def test_reads_geojson_and_writes_stable_csv_columns(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {"SA1_CODE21": ["10101000001"], "STE_CODE21": ["1"]},
            geometry=[box(150.1, -33.9, 150.9, -33.1)],
            crs="EPSG:4326",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sa1_path = root / "sa1.geojson"
            lhd_path = root / "lhd.geojson"
            output_path = root / "mapping.csv"
            sa1s.to_file(sa1_path, driver="GeoJSON")
            self.lhds.to_file(lhd_path, driver="GeoJSON")

            count = build_mapping(sa1_path, lhd_path, output_path)
            header = output_path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(
                (root / "mapping_excluded.csv").read_text().strip(),
                "sa1_code_2021,exclusion_reason",
            )

        self.assertEqual(count, 1)
        self.assertEqual(
            header,
            "sa1_code_2021,lhd_code,lhd_name,overlap_pct,assignment_method",
        )

    def test_writes_excluded_codes_and_reasons_to_separate_csv(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {
                "SA1_CODE21": ["10101000001", "10101000002", "19999949999"],
                "STE_CODE21": ["1", "1", "1"],
            },
            geometry=[box(150.1, -33.9, 150.9, -33.1), box(140, -34, 141, -33), None],
            crs="EPSG:4326",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sa1_path, lhd_path = root / "sa1.geojson", root / "lhd.geojson"
            output_path = root / "mapping.csv"
            excluded_path = root / "audit" / "excluded.csv"
            sa1s.to_file(sa1_path, driver="GeoJSON")
            self.lhds.to_file(lhd_path, driver="GeoJSON")
            with self.assertWarns(UserWarning):
                self.assertEqual(build_mapping(sa1_path, lhd_path, output_path, excluded_path), 1)
            with excluded_path.open() as source:
                exclusions = list(csv.DictReader(source))
            self.assertEqual(exclusions, [
                {"sa1_code_2021": "10101000002", "exclusion_reason": "no_positive_area_lhd_overlap"},
                {"sa1_code_2021": "19999949999", "exclusion_reason": "nonspatial_record"},
            ])
            with output_path.open() as source:
                self.assertEqual(
                    [row["sa1_code_2021"] for row in csv.DictReader(source)],
                    ["10101000001"],
                )
            boundaries = gpd.read_file(root / "nsw_sa1_boundaries.geojson")
            self.assertEqual(boundaries.crs.to_epsg(), 4326)
            self.assertEqual(
                set(boundaries["SA1_CODE21"]), {"10101000001", "10101000002"}
            )
            self.assertFalse(boundaries.geometry.isna().any())
            with self.assertRaisesRegex(ValueError, "different output paths"):
                build_mapping(sa1_path, lhd_path, output_path, output_path)

    def test_shapefile_and_nested_zip_match_geojson_mapping(self) -> None:
        sa1s = gpd.GeoDataFrame(
            {
                "SA1_CODE21": ["10101000001", "10101000002", "20101000001"],
                "STE_CODE21": ["1", "1", "2"],
            },
            geometry=[
                box(150.1, -33.9, 150.9, -33.1),
                box(150.8, -33.9, 151.6, -33.1),
                box(145.0, -34.0, 146.0, -33.0),
            ],
            crs="EPSG:4326",
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shape_dir = root / "shape"
            shape_dir.mkdir()
            shapefile = shape_dir / "SA1_2021_AUST_GDA2020.shp"
            sa1s.to_file(shapefile, driver="ESRI Shapefile")
            geojson = root / "sa1.geojson"
            sa1s.to_file(geojson, driver="GeoJSON")
            lhd_path = root / "lhd.geojson"
            self.lhds.to_file(lhd_path, driver="GeoJSON")
            zipped = root / "SA1 archive.zip"
            with ZipFile(zipped, "w") as archive:
                for component in shape_dir.iterdir():
                    archive.write(component, f"nested/{component.name}")
            original_zip = zipped.read_bytes()
            outputs = []
            for source in (geojson, shapefile, zipped):
                output = root / "mapping.csv"
                self.assertEqual(build_mapping(source, lhd_path, output), 2)
                outputs.append(output.read_text())
                boundaries = gpd.read_file(root / "nsw_sa1_boundaries.geojson")
                self.assertEqual(set(boundaries["SA1_CODE21"]), {"10101000001", "10101000002"})
                self.assertEqual(boundaries.crs.to_epsg(), 4326)
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], outputs[2])
            self.assertEqual(zipped.read_bytes(), original_zip)

    def test_rejects_zip_without_a_unique_shapefile(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.zip"
            for members in (("readme.txt",), ("one.shp", "two.shp")):
                with self.subTest(members=members):
                    with ZipFile(path, "w") as archive:
                        for member in members:
                            archive.writestr(member, b"")
                    with self.assertRaisesRegex(ValueError, "exactly one shapefile"):
                        read_sa1_layer(path)


if __name__ == "__main__":
    unittest.main()
