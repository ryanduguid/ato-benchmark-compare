from __future__ import annotations

import json
from decimal import Decimal

import pytest

from atobenchmark import dataset as ds


def test_both_years_ship_and_load() -> None:
    years = ds.available_years()
    assert "2023-24" in years
    assert "2022-23" in years
    for year in years:
        data = ds.load(year)
        assert data.year == year
        assert len(data.business_types) == 100


def test_default_year_is_the_most_recent() -> None:
    assert ds.load().year == max(ds.available_years())


def test_bakery_matches_the_published_ato_page() -> None:
    """Cross check against ato.gov.au QC 43659, Bakeries and hot bread shops, 2023-24.

    Published there as: cost of sales 31% to 38%, 34% to 39%, 29% to 36%; total
    expenses 69% to 81%, 75% to 86%, 82% to 90%.
    """
    bakery = ds.load("2023-24").get("Bakeries and hot bread shops")
    assert bakery.key_ratio == "cost_of_sales_to_turnover"
    published = {
        "low": (("0.31", "0.38"), ("0.69", "0.81")),
        "medium": (("0.34", "0.39"), ("0.75", "0.86")),
        "high": (("0.29", "0.36"), ("0.82", "0.90")),
    }
    for band in bakery.bands:
        cost_of_sales, total_expenses = published[band.band]
        assert band.ratios["cost_of_sales_to_turnover"].minimum == Decimal(cost_of_sales[0])
        assert band.ratios["cost_of_sales_to_turnover"].maximum == Decimal(cost_of_sales[1])
        assert band.ratios["total_expenses_to_turnover"].minimum == Decimal(total_expenses[0])
        assert band.ratios["total_expenses_to_turnover"].maximum == Decimal(total_expenses[1])
    assert [band.label for band in bakery.bands] == [
        "$65,000 - $400,000",
        "$400,001 - $750,000",
        "More than $750,000",
    ]


def test_service_industry_has_no_cost_of_sales_benchmark() -> None:
    architects = ds.load("2023-24").get("Architectural services")
    assert architects.key_ratio == "total_expenses_to_turnover"
    assert all(band.ratios["cost_of_sales_to_turnover"] is None for band in architects.bands)


def test_two_band_industry_is_handled() -> None:
    barber = ds.load("2023-24").get("Barber and men's hairdressing")
    assert len(barber.bands) == 2
    assert barber.bands[-1].turnover_to is None


@pytest.mark.parametrize(
    ("turnover", "expected"),
    [
        ("65000", "low"),
        ("400000", "low"),
        ("400000.50", "medium"),
        ("400001", "medium"),
        ("750000", "medium"),
        ("750000.01", "high"),
        ("9000000", "high"),
    ],
)
def test_band_selection_covers_the_printed_boundaries(turnover: str, expected: str) -> None:
    # The ATO prints "$65,000 - $400,000" then "$400,001 - $750,000". Read literally
    # that leaves $400,000.50 in no band at all, so the bands are treated as adjoining.
    bakery = ds.load("2023-24").get("Bakeries and hot bread shops")
    band = bakery.band_for(Decimal(turnover))
    assert band is not None
    assert band.band == expected


def test_turnover_below_the_lowest_band_has_no_band() -> None:
    bakery = ds.load("2023-24").get("Bakeries and hot bread shops")
    assert bakery.band_for(Decimal("64999.99")) is None


def test_bands_do_not_overlap_anywhere_in_either_year() -> None:
    for year in ds.available_years():
        for business_type in ds.load(year).business_types:
            previous_to = None
            for band in business_type.bands:
                if previous_to is not None:
                    assert band.turnover_from == previous_to, f"{year} {business_type.name}"
                    assert band.turnover_from_inclusive is False
                if band.turnover_to is not None:
                    assert band.turnover_to > band.turnover_from
                previous_to = band.turnover_to
            assert business_type.bands[-1].turnover_to is None


def test_every_published_range_is_plausible() -> None:
    for year in ds.available_years():
        for business_type in ds.load(year).business_types:
            for band in business_type.bands:
                for benchmark in band.ratios.values():
                    if benchmark is None:
                        continue
                    assert Decimal(0) < benchmark.minimum <= benchmark.maximum <= Decimal(1)


def test_key_ratio_follows_whether_cost_of_sales_is_published() -> None:
    for year in ds.available_years():
        for business_type in ds.load(year).business_types:
            has_cost_of_sales = any(
                band.ratios["cost_of_sales_to_turnover"] for band in business_type.bands
            )
            expected = "cost_of_sales_to_turnover" if has_cost_of_sales else "total_expenses_to_turnover"
            assert business_type.key_ratio == expected


def test_search_is_punctuation_and_case_insensitive() -> None:
    data = ds.load("2023-24")
    assert data.get("barber and men's hairdressing").name.startswith("Barber")
    assert data.get("BARBER AND MEN’S HAIRDRESSING").name.startswith("Barber")
    assert data.get("fish and chips").name == "Fish and chips shops"


def test_ambiguous_name_lists_the_candidates() -> None:
    data = ds.load("2023-24")
    with pytest.raises(ds.DatasetError) as excinfo:
        data.get("cleaning services")
    assert "matches more than one" in str(excinfo.value)


def test_unknown_name_points_at_the_industries_command() -> None:
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.load("2023-24").get("interstellar freight")
    assert "industries" in str(excinfo.value)


def test_unknown_year_lists_what_is_available() -> None:
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.load("1999-00")
    assert "Available" in str(excinfo.value)


def test_future_schema_version_is_refused() -> None:
    payload = json.loads((ds.DATA_DIR / "benchmarks-2023-24.json").read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.loads(json.dumps(payload))
    assert "schema version" in str(excinfo.value)


def test_non_object_business_type_is_refused() -> None:
    payload = json.loads((ds.DATA_DIR / "benchmarks-2023-24.json").read_text(encoding="utf-8"))
    payload["business_types"] = ["oops"]
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.loads(json.dumps(payload))
    assert "not an object" in str(excinfo.value)


def test_non_object_turnover_band_is_refused() -> None:
    payload = json.loads((ds.DATA_DIR / "benchmarks-2023-24.json").read_text(encoding="utf-8"))
    payload["business_types"][0]["turnover_bands"] = ["oops"]
    with pytest.raises(ds.DatasetError) as excinfo:
        ds.loads(json.dumps(payload))
    assert "not an object" in str(excinfo.value)


def test_reversed_range_is_refused() -> None:
    payload = json.loads((ds.DATA_DIR / "benchmarks-2023-24.json").read_text(encoding="utf-8"))
    payload["business_types"][0]["turnover_bands"][0]["total_expenses_to_turnover"] = {
        "min": "0.90",
        "max": "0.10",
    }
    with pytest.raises(ds.DatasetError):
        ds.loads(json.dumps(payload))


def test_provenance_is_recorded() -> None:
    source = ds.load("2023-24").source
    assert source["publisher"] == "Australian Taxation Office"
    assert source["licence"].startswith("Creative Commons Attribution 2.5")
    assert len(source["sha256"]) == 64
    assert source["resource_url"].startswith("https://data.gov.au/")


def test_ratios_are_exact_decimals_not_floats() -> None:
    bakery = ds.load("2023-24").get("Bakeries and hot bread shops")
    value = bakery.bands[0].ratios["cost_of_sales_to_turnover"].minimum
    assert isinstance(value, Decimal)
    assert value == Decimal("0.31")
    assert str(value) == "0.31"
