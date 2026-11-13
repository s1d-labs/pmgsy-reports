"""What this family scrapes: the open citizen reports of PMGSY's OMMAS (pmgsy.dord.gov.in, Home -> Citizen page) that the
contractor-registration family does not hold. That family has the contractor master, works awarded, core network roads,
completed roads and maintenance spending (adapters/pmgsy*.py there); this one adds the road list with contractor names and
progress, sanctioned projects, habitations, PMGSY-III facilities, quality monitor inspections, final bill status and joint
inspections. Code: adapters/ommas.py. No login, no captcha. The site answers Indian addresses only: proxy="IN".

Not collected (walls): Final Bill Payment per agency (its agency list answers 401 to the public); eMARG road details (captcha);
the officials' OMMAS (pmgsygov.dord.gov.in, login). Inspection photos are kept as links (PHOTOS=1 in a project's env stores them).
"""
import gzip
import json
import time
from pathlib import Path

HERE = Path(__file__).parent
FAMILY = json.loads((HERE / "scrape.json").read_text())["family"]
SITE = "https://pmgsy.dord.gov.in"
TARGET = "pmgsy.dord.gov.in"
YEAR = time.gmtime().tm_year

STATES = {1: "Andaman And Nicobar (UT)", 2: "Andhra Pradesh", 3: "Arunachal Pradesh", 4: "Assam", 5: "Bihar",
          6: "Chandigarh (UT)", 7: "Chhattisgarh", 8: "Dadra And Nagar Haveli (UT)", 9: "Daman And Diu (UT)", 10: "Delhi (UT)",
          11: "Goa", 12: "Gujarat", 13: "Haryana", 14: "Himachal Pradesh", 15: "Jammu And Kashmir", 16: "Jharkhand",
          17: "Karnataka", 18: "Keralam", 19: "Lakshadweep (UT)", 20: "Madhya Pradesh", 21: "Maharashtra", 22: "Manipur",
          23: "Meghalaya", 24: "Mizoram", 25: "Nagaland", 26: "Odisha", 27: "Puducherry (UT)", 28: "Punjab", 29: "Rajasthan",
          30: "Sikkim", 31: "Tamil Nadu", 32: "Tripura", 33: "Uttar Pradesh", 34: "Uttarakhand", 35: "West Bengal",
          36: "Telangana", 37: "Ladakh (UT)"}      # the site's state codes
# the site's districts per state (DistrictDetails, 2026-11-13): [state code, district code, name]; 792 districts
DISTRICTS = [tuple(x) for x in json.loads(gzip.decompress((HERE / "queues" / "pmgsy_districts.json.gz").read_bytes()))]
# executing agencies with works in the Final Bill Payment Abstract (2026-11-13)
AGENCIES = ["Andaman And Nicobar ( PWD )", "Andhra Pradesh ( PR )", "Andhra Pradesh ( R&B )", "Arunachal Pradesh ( RWD )",
            "Assam ( PWD )", "Bihar ( CPWD )", "Bihar ( IRCON )", "Bihar ( NBCC )", "Bihar ( NHPC )", "Bihar ( NPCC )",
            "Bihar ( RCD )", "Bihar ( RWD )", "Chhattisgarh ( CRRDA )", "Chhattisgarh ( PWD )", "Goa ( DRDA(GOA) )",
            "Gujarat ( NHPC )", "Haryana ( PWD )", "Himachal Pradesh ( PWD )", "Jammu And Kashmir ( J&KRRDA )",
            "Jharkhand ( IRCON )", "Jharkhand ( REO )", "Karnataka ( KRRDA )", "Keralam ( PR )", "Ladakh ( LRRDA )",
            "Madhya Pradesh ( MPRRDA )", "Maharashtra ( MRRDA )", "Maharashtra ( PWD )", "Manipur ( PWD )", "Meghalaya ( MPWA )",
            "Mizoram ( PWD )", "Nagaland ( SR&B )", "Odisha ( R&B )", "Odisha ( RWD )", "Puducherry ( LDA )", "Punjab ( PWD )",
            "Rajasthan ( RRRDA )", "Sikkim ( PWD )", "Tamil Nadu ( RD )", "Telangana ( PR )", "Telangana ( R&B )",
            "Tripura ( PWD )", "Uttar Pradesh ( PWD )", "Uttarakhand ( PWD )", "West Bengal ( WBSRDA )"]


# max_workers: the hub compares it with every worker on the target (contractor-registration's PMGSY projects too), so it is the
# ceiling for the whole site, not for this project: 4 = at most 4 workers on pmgsy.dord.gov.in when ours run (the family borrows
# at most 4 proxies anyway).
def _env(**extra):
    return {"ADAPTER": "ommas", "SITE": SITE, **extra}


def _district(report, **extra):
    return [{"key": f"{report}-{s:02d}-{d}" + "".join(f"-{v}" for v in extra.values()),
             "payload": {"report": report, "state": s, "state_name": STATES[s], "district": d, "district_name": name, **extra}}
            for s, d, name in DISTRICTS]


def _common(name, title, unit, record, items, workers=4, **env):
    return {"name": f"{FAMILY}-{name}", "title": title, "task": "crawl", "target": TARGET, "env": _env(**env), "proxy": "IN",
            "max_workers": workers, "unit_label": unit, "record_label": record, "items": items}


PROJECTS = [
    # Proposals -> State-wise List of Roads, "Road Wise": every work with contractor name, expenditure, stage, completion.
    # Schemes 1-4 (PMGSY-I, -II, RCPLWEA, PMGSY-III) per district; 5-7 (VVP, PM-JANMAN, PMGSY-IV) per state.
    _common("roads", "PMGSY works per district and scheme: road, habitations, cost, expenditure, stage, contractor, completion",
            "district x scheme", "work",
            [i for sc in (1, 2, 3, 4) for i in _district("roads", scheme=sc)]
            + [{"key": f"roads-{s:02d}-0-{sc}", "payload": {"report": "roads", "state": s, "state_name": n, "scheme": sc}}
               for sc in (5, 6, 7) for s, n in STATES.items()]),
    _common("sanctioned", "PMGSY sanctioned projects per district and scheme: cost breakdown, habitations with population",
            "district x scheme", "work x habitation", [i for sc in (1, 2, 3, 4) for i in _district("sanctioned", scheme=sc)]),
    _common("habitations", "PMGSY habitation-wise core network per district: habitation, road number, route, length, population",
            "district", "habitation x road", _district("habitations")),
    _common("facilities", "PMGSY-III rural facilities survey (2020) per district: facility, category, habitation, GPS",
            "district", "facility", _district("facilities")),
    # Works-wise Grading Abstract + every NQM / SQM inspection (monitor, date, items graded, photo links, GPS points)
    _common("qm", "PMGSY quality monitor inspections per district (NQM, SQM): monitor, date, item grades, photos, GPS",
            "district", "work or inspection", _district("qm")),
    _common("monitors", "PMGSY monitor-wise grading abstract per year (NQM, SQM): monitor, month, district, gradings",
            "year x tier", "monitor x month x district",
            [{"key": f"monitors-{qm}-{y}", "payload": {"report": "monitors", "qm": qm, "year": y}}
             for qm in ("NQM", "SQM") for y in range(YEAR, 1999, -1)]),
    _common("finalbill", "PMGSY final bill status per agency: works physically complete, financial completion pending or done",
            "agency", "work", [{"key": f"fb-{k:02d}", "payload": {"report": "finalbill", "agency": a}} for k, a in enumerate(AGENCIES)]),
    _common("ji", "PMGSY joint inspections with public representatives per year: work, date, grading, report PDF",
            "year", "inspection", [{"key": f"ji-{y}", "payload": {"report": "ji", "year": y}} for y in range(YEAR, 2004, -1)]),
]

PROBES = []
