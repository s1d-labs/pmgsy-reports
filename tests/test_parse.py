"""Parsing tests on pages saved from pmgsy.dord.gov.in (2026-11-13)."""
import gzip
from pathlib import Path

from adapters import ommas

PAGES = Path(__file__).parent / "pages"


def read(name):
    p = PAGES / name
    data = p.read_bytes()
    return (gzip.decompress(data) if name.endswith(".gz") else data).decode("utf-8", "replace")


def test_roads_csv():
    rows = ommas.parse_csv(read("roads.csv"))
    assert len(rows) == 196
    r = rows[0]
    assert r["ims_package_id1"] == "BM0201" and r["road_name"] == "MANGTHANG"
    assert "cont_name1" in r and r["cont_name1"].startswith("PWD Sikkim")
    assert "Sikkim" in ommas.heading(read("roads.csv"))


def test_facilities_csv():
    rows = ommas.parse_csv(read("facilities.csv"))
    assert len(rows) == 174
    assert {"habitation_name", "hab_code", "latitude", "longitude", "sub_category"} <= set(rows[0])


def test_inspection_list():
    dates, obs = ommas.inspection_list(read("inspectiongradingdetails.html"))
    assert len(dates) == 4 and [o for o, _ in obs] == ["451232", "510215", "579193", "375965"]


def test_inspection_details():
    d = ommas.parse_inspection(read("inspdetails.html"))
    assert d["head"]["Monitor Name"] == "Singh Haobijam Nilamani" and d["head"]["Inspection Date"] == "19 Sep 2022"
    assert d["items"][-1] == {"item": "Overall Grading", "grade": "Satisfactory"}
    assert len(d["photos"]) == 14 and d["photos"][0]["url"].startswith("https://pmgsygov.dord.gov.in/OMMASII/QM/SQM/")
    assert d["photos"][0]["lat"].startswith("27.31") and len(d["points"]) == 14


def test_qmworks_links():
    links = ommas.inspection_links(read("qmworks_viewer.html.gz"))
    assert ("200724", "S") in links and ("213098", "I") in links
    assert ommas.leaf_name(read("qmworks_viewer.html.gz")) == "NQM_SQM_Mon_Works_Detail_Report"


def test_finalbill_cells():
    cells = ommas.finalbill_cells(read("finalbill_viewer.html.gz"))
    assert len(cells) == 915
    an = [c for c in cells if c[0] == "Andaman And Nicobar ( PWD )"]
    assert len(an) == 18 and an[0][1:3] == ("2020", "I") and an[2][2] == "III"
