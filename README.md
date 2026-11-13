# pmgsy-reports

A family of the s1d-labs scrape pool: the open citizen reports of PMGSY's OMMAS (pmgsy.dord.gov.in, Home -> Citizen page) that
s1d-labs/contractor-registration does not hold. That family has the contractor master, works awarded, core network roads,
completed roads and maintenance spending. Code: `adapters/ommas.py` (its docstring lists every field). Projects: `family.py`.

| Project | Report | Item | Records |
|---|---|---|---|
| `pmgsy-reports-roads` | State-wise List of Roads, road wise | district x scheme | work: habitations, cost, expenditure, stage, contractor, completion |
| `pmgsy-reports-sanctioned` | Sanctioned Projects | district x scheme | work x habitation: cost breakdown, population, SC/ST |
| `pmgsy-reports-habitations` | Habitation-wise Core Network | district | habitation x core network road |
| `pmgsy-reports-facilities` | Facility Details (PMGSY-III survey 2020) | district | facility with GPS |
| `pmgsy-reports-qm` | Works-wise Grading Abstract + every NQM / SQM inspection | district | work; inspection: monitor, date, item grades, photo links, GPS points |
| `pmgsy-reports-monitors` | Monitor-wise Grading Abstract (NQM, SQM) | year x tier | monitor x month x district |
| `pmgsy-reports-finalbill` | Final Bill Payment Abstract, every cell drilled | agency | work: length, cost, expenditure, completion |
| `pmgsy-reports-ji` | Joint Inspection with Public Representatives | year | inspection + report PDF |

Run one item without the hub (from an Indian address): `python crawl.py --local ommas '{"report": "roads", "state": 30,
"state_name": "Sikkim", "district": 164, "district_name": "East", "scheme": 1}'`. Tests: `python -m pytest tests -q`.

Not collected: Final Bill Payment per agency (its agency list answers 401 to the public), eMARG road details (captcha), the
officials' OMMAS (login). Inspection photos are links only unless a project's env has `PHOTOS=1`.
