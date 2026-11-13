"""PMGSY / OMMAS citizen reports (pmgsy.dord.gov.in, Home -> Citizen page). No login, no captcha.

Every report is an SSRS report behind an ASP.NET MVC page: the report form (cookie + anti-forgery token), the report POST (answers
an iframe address), the report viewer (HTML), then the viewer's CSV export. Upper levels of a report are summaries; the reader
clicks a row (a "drill-through", a postback of the viewer's form) to go one level down. The viewer renders on the server only for a
client it does not take for a browser, so the User-Agent stays the scraper's own (adapters/common.py UA).

Queue item payload: {"report": <name in REPORTS or "qm" / "finalbill" / "ji">, ...}. Every export is stored as it came
(docs/<report>/...csv) and every row becomes a record with every column of the export (the SSRS field names, lower case).

Reports (menu of the citizen page -> report):
  roads        Proposals -> State-wise List of Roads, "Road Wise" ticked: every work of a district and scheme: block, package,
               sanction year, road / bridge, new or upgrade, habitations served, core network road and habitations, state cost,
               expenditure till date, stage of progress, collaboration, CONTRACTOR NAME (with agreement date), maintenance cost,
               completion date (financial / physical). Payload {state, state_name, district, district_name, scheme}.
  sanctioned   Proposals -> Sanctioned Projects, per district and scheme, each block: per work the pavement length and cost, CD
               works, protection and other works, LSB, state cost, total cost, collaboration, habitations connected with
               population and SC/ST population.
  habitations  Core Network -> Habitation-wise Core Network, per district, each block: habitation, road number, road name,
               route, chainage, full / partial, length, from, to, population.
  facilities   Other Reports -> Facility Details (PMGSY-III rural facilities survey, 2020): habitation, facility, address,
               category, sub-category, latitude, longitude. Per district.
  monitors     Quality Monitoring -> Monitor-wise Grading Abstract (NQM or SQM), per calendar year: monitor, year, month,
               state, district, number of works graded S / SRI / U for completed, ongoing, maintenance and bridge works.
  qm           Quality Monitoring -> Works-wise Grading Abstract, per district, January 2000 to now: per work the number of
               NQM and SQM inspections and the latest grade; then, for every work, every inspection (InspectionGradingDetails ->
               InspDetails): monitor name, inspection date, state, district, block, package, sanction year, road, type,
               status, length, each item graded (quality arrangements, embankment, base, surface, signs, ... overall), the
               photo links with captions, the geo-tagged points. Raw inspection pages go to the bucket as one .jsonl.gz per item.
               Photos: links only, unless the project env has PHOTOS=1 (then each photo is stored under photos/).
  finalbill    Financial Progress -> Final Bill Payment Abstract, per executing agency (state department): every nonzero cell
               (period x I / II / III) drilled through to its works: district, package, road, connectivity, length, sanction
               cost, expenditure, physical completion. I = physically complete, financial completion pending; II = the same,
               pending more than 6 months; III = physically completed works.
  ji           Quality Monitoring -> Joint Inspection with Public Representatives, per calendar year: state, district, block,
               year, package, work, inspection date, grading; the inspection report PDF of each row is stored.
"""
import csv
import gzip
import html
import io
import json
import os
import re
import time

from adapters.common import clean, sha1

SITE = "https://pmgsy.dord.gov.in"
LEAF_TIMEOUT = 900
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DRILL = re.compile(r"Drillthrough','([^']+)'")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December"]
PHOTOS = os.environ.get("PHOTOS") == "1"


# ---------------------------------------------------------------- pure helpers (tested on saved pages)

def token(page):
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page or "")
    return m.group(1) if m else None


def iframe_src(page):
    m = re.search(r'<iframe[^>]+src="([^"]+)"', page or "", re.I)
    return SITE + html.unescape(m.group(1)) if m else None


def export_url(viewer, fmt="CSV"):
    m = re.search(r'"ExportUrlBase":"([^"]+)"', viewer or "")
    return SITE + m.group(1).replace("\\u0026", "&") + fmt if m else None


def leaf_name(viewer):
    m = re.search(r'"ExportUrlBase":"[^"]*?FileName=([^&"\\]+)', viewer or "")
    return m.group(1) if m else None


def drill_ids(viewer):
    """The drill-through cells of a report page, in row order ("65iT0R0x2:0" is row 2)."""
    ids = set(DRILL.findall(viewer or ""))
    return sorted(ids, key=lambda a: [int(n) for n in re.findall(r"\d+", a)])


def report_form(viewer):
    """(action url, fields) of the report viewer's form: every hidden field, as a browser would post it back."""
    m = re.search(r'<form[^>]*action="\./([^"]+)"', viewer or "")
    if not m:
        return None, {}
    fields = {}
    for tag in re.findall(r"<input[^>]*type=\"hidden\"[^>]*>", viewer):
        n, v = re.search(r'name="([^"]+)"', tag), re.search(r'value="([^"]*)"', tag)
        if n:
            fields[n.group(1)] = html.unescape(v.group(1)) if v else ""
    return SITE + "/" + html.unescape(m.group(1)), fields


def drill_form(viewer, action):
    url, fields = report_form(viewer)
    if not url or f"'Drillthrough','{action}'" not in viewer:
        return None, {}
    return url, {**fields, "__EVENTTARGET": "ReportViewer$ctl03", "__EVENTARGUMENT": "",
                 "ReportViewer$ctl03$ctl00": "Drillthrough", "ReportViewer$ctl03$ctl01": action,
                 "ReportViewer$ctl09$ReportControl$ctl02": "Drillthrough", "ReportViewer$ctl09$ReportControl$ctl03": action}


def form_defaults(page):
    """The report form's fields as the page sets them: hidden and text inputs, the selected option of each list."""
    m = re.search(r"<form[^>]*>(.*?)</form>", page or "", re.S)
    body = m.group(1) if m else (page or "")
    f = {}
    for tag in re.findall(r"<input[^>]*>", body):
        n, v, t = re.search(r'name="([^"]+)"', tag), re.search(r'value="([^"]*)"', tag), re.search(r'type="([^"]+)"', tag)
        if n and (not t or t.group(1) in ("hidden", "text")):
            f.setdefault(n.group(1), html.unescape(v.group(1)) if v else "")
    for name, inner in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', body, re.S):
        opts = re.findall(r'<option([^>]*)value="([^"]*)"', inner)
        sel = [o for o in opts if "selected" in o[0]] or opts[:1]
        if sel:
            f[name] = sel[0][1]
    return f


def parse_csv(text):
    """The detail rows of an SSRS CSV export as dicts {field name (lower case): value}. The export has a few sections, each a
    row of field names and then its rows; the detail section is the one with the most rows. A TextboxN column that holds the
    same caption on every row is left out (the stored CSV keeps it)."""
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    best, i = None, 0
    while i < len(rows):
        head = rows[i]
        if head and all(IDENT.match(c or "") for c in head):
            j = i + 1
            while j < len(rows) and len(rows[j]) == len(head) and not all(IDENT.match(c or "") for c in rows[j]):
                j += 1
            body = rows[i + 1:j]
            if body and (best is None or len(body) > len(best[1])):
                best = (head, body)
            i = j
        else:
            i += 1
    if not best:
        return []
    head, body = best
    keep = []
    for k, h in enumerate(head):
        vals = [r[k] for r in body]
        if h.startswith("Textbox") and len(body) > 1 and len(set(vals)) == 1 and not re.match(r"^[\d.,\s%-]*$", vals[0]):
            continue                                    # a caption repeated on every row
        keep.append(k)
    out = []
    for r in body:
        d = {}
        for k in keep:
            name = head[k].lower()
            d[name if name not in d else f"{name}_{k}"] = r[k].strip()
        out.append(d)
    return out


def heading(text):
    """The report's title lines (the first section's values), e.g. "State: Sikkim District: East ..."."""
    rows = list(csv.reader(io.StringIO((text or "").lstrip("﻿"))))
    return re.sub(r"\s+", " ", " | ".join(rows[1])).strip() if len(rows) > 1 else ""


def cell_text(s):
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", s or "")))


def table_rows(viewer):
    """[[cell text or (text, drill id, link)]] for every table row of a rendered report page."""
    out = []
    for tr in re.findall(r"<TR[^>]*>(.*?)</TR>", viewer or "", re.S | re.I):
        cells = []
        for c in re.findall(r"<TD[^>]*>(.*?)</TD>", tr, re.S | re.I):
            d = re.search(r"Drillthrough','([^']+)'", c)
            a = re.search(r'href="(https?://[^"]+)"', c)
            cells.append((cell_text(c), d.group(1) if d else None, html.unescape(a.group(1)) if a else None))
        out.append(cells)
    return out


def inspection_links(viewer):
    """[(road code, "S" | "I")] of the Works-wise Grading Abstract: the inspection pages of each work, in page order."""
    seen, out = set(), []
    for code, kind in re.findall(r"InspectionGradingDetails\?id=(\d+)\$([SI])", viewer or ""):
        if (code, kind) not in seen:
            seen.add((code, kind))
            out.append((code, kind))
    return out


def inspection_list(page):
    """(dates, observation ids) of a work's inspection page: the inspection dates and the observations (chainage list)."""
    def opts(name):
        m = re.search(rf'<select[^>]*id="{name}"[^>]*>(.*?)</select>', page or "", re.S)
        return [(v, cell_text(t)) for v, t in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), re.S)] if m else []
    return opts("INSP_DATE"), opts("CHAINAGE")


def parse_inspection(page):
    """One inspection (InspDetails): the head table (monitor, date, state, ..., length), the items graded, the photos (link,
    caption, lat/long, date) and the geo-tagged points. Pure."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page or "", re.S | re.I):
        cells = [cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if any(cells):
            rows.append(cells)
    head, items, points = {}, [], []
    mode = "head"
    for cells in rows:
        if cells[:3] == ["Sr.No.", "Item Description", "Grade"]:
            mode = "items"
            continue
        if cells[:3] == ["Sr No", "Latitude", "Longitude"]:
            mode = "points"
            continue
        if mode == "head":
            for k in range(0, len(cells) - 1, 2):
                if cells[k] and cells[k] not in head:
                    head[cells[k]] = cells[k + 1]
        elif mode == "items" and len(cells) >= 3 and cells[0].isdigit():
            items.append({"item": cells[1], "grade": cells[2], **({"extra": cells[3:]} if len(cells) > 3 else {})})
        elif mode == "points" and len(cells) >= 3 and cells[0].isdigit():
            points.append([cells[1], cells[2]])
    photos = []
    for tag in re.findall(r"<img[^>]*id='qmimage\d+'[^>]*>|<img[^>]*id=\"qmimage\d+\"[^>]*>", page or ""):
        src = re.search(r"src=['\"]([^'\"]+)['\"]", tag)
        title = re.search(r"title=['\"]([^'\"]*)['\"]", tag)
        ll = re.search(r"data-latlong=['\"]([^'\"]*)['\"]", tag)
        dt = re.search(r"data-date=['\"]([^'\"]*)['\"]", tag)
        lat, _, lon = (ll.group(1) if ll else "").partition("$$")
        photos.append({"url": html.unescape(src.group(1)) if src else "", "caption": html.unescape(title.group(1)) if title else "",
                       "lat": lat, "lon": lon, "date": dt.group(1) if dt else ""})
    return {"head": head, "items": items, "photos": photos, "points": points}


def finalbill_cells(viewer):
    """[(agency, period, column, drill id)] for every drillable cell of the Final Bill Payment Abstract. Columns after the
    agency name come in threes (I, II, III) per period; the last three (Total) are not drillable."""
    periods = ["2000-2005", "2006-2010", "2011-2015"] + [str(y) for y in range(2016, 2100)]
    out = []
    for cells in table_rows(viewer):
        if not any(d for _, d, _ in cells):
            continue
        texts = [t for t, _, _ in cells]
        start = next((k for k, t in enumerate(texts) if t and not t.replace(",", "").isdigit()), None)
        if start is None:
            continue
        agency = texts[start]
        for k, (t, d, _) in enumerate(cells[start + 1:]):
            if d:
                out.append((agency, periods[k // 3] if k // 3 < len(periods) else "?", ("I", "II", "III")[k % 3], d))
    return out


# ---------------------------------------------------------------- site access

def start_session(ctx):
    """The citizen page sets the session's cookies (scheme, state); without them some viewers leave out their drill-throughs."""
    if "globalPMGSYScheme" not in ctx.session.cookies.keys():
        return ctx.request("GET", SITE + "/Home/CitizenPage", timeout=60) is not None
    return True


def viewer(ctx, layout, report, fields):
    """The report viewer page of one report: form (token + defaults), report POST (iframe address), viewer."""
    page = ctx.request("GET", SITE + layout, timeout=60, headers={"X-Requested-With": "XMLHttpRequest"})
    tok = token(page.text if page is not None else "")
    if not tok:
        return None
    data = {**form_defaults(page.text), **{k: str(v) for k, v in fields.items()}, "__RequestVerificationToken": tok}
    rep = ctx.request("POST", SITE + report, timeout=600, data=data,
                      headers={"X-Requested-With": "XMLHttpRequest", "Referer": SITE + "/Home/CitizenPage"})
    src = iframe_src(rep.text if rep is not None else "")
    if not src:
        return None
    v = ctx.request("GET", src, timeout=LEAF_TIMEOUT)
    return v.text if v is not None else None


def drill(ctx, v, action):
    url, form = drill_form(v or "", action)
    if not url:
        return None
    r = ctx.request("POST", url, data=form, timeout=LEAF_TIMEOUT)
    return r.text if r is not None else None


def export(ctx, v):
    url = export_url(v)
    r = ctx.request("GET", url, timeout=LEAF_TIMEOUT) if url else None
    return r.content if r is not None else None


def walk(ctx, spec, fields):
    """[(drill path, leaf viewer)] for every leaf the spec's path reaches; None when the site does not answer.
    "first" clicks the first row of a level, "each" every row (each from a fresh viewer: a drill-through changes its state)."""
    leaves, path = [], spec["path"]

    def at(prefix):
        v = viewer(ctx, spec["layout"], spec["report"], fields)
        for a in prefix:
            if v is None:
                return None
            v = drill(ctx, v, a)
        return v

    def go(prefix, v, depth):
        if depth == len(path):
            leaves.append((prefix, v))
            return True
        ids = drill_ids(v)
        if path[depth] == "first":
            ids = ids[:1]
        for n, a in enumerate(ids):
            vv = v if n == 0 else at(prefix)
            nxt = drill(ctx, vv, a) if vv is not None else None
            if nxt is None or not go(prefix + [a], nxt, depth + 1):
                return False
        return True

    top = viewer(ctx, spec["layout"], spec["report"], fields)
    if top is None or not go([], top, 0):
        return None
    return leaves


# ---------------------------------------------------------------- the reports

def _place(p):
    return {"StateCode": p["state"], "StateName": p["state_name"], "DistrictCode": p.get("district", 0),
            "DistName": p.get("district_name", "All Districts"), "BlockCode": 0, "BlockName": "All Blocks"}


def _period(p):
    y0, y1 = p.get("from_year", 2000), p.get("to_year", time.localtime().tm_year)
    m0, m1 = p.get("from_month", 1), p.get("to_month", time.localtime().tm_mon if y1 == time.localtime().tm_year else 12)
    return {"FrmMonth": m0, "FrmYear": y0, "ToMonth": m1, "ToYear": y1, "FromMonthName": MONTHS[m0 - 1], "FromYearName": y0,
            "ToMonthName": MONTHS[m1 - 1], "ToYearName": y1}


REPORTS = {
    "roads": {"layout": "/ProposalArea/Proposal/StateListWiseRoadsLayout", "report": "/ProposalArea/Proposal/StateListWiseRoadsReport/",
              "fields": lambda p: {**_place(p), "Year": 0, "Batch": 0, "BatchName": "All Batches", "FundingAgency": 0,
                                   "CollaborationName": "All Collaborations", "Status": "%", "StatusName": "All Status",
                                   "Scheme": p["scheme"], "SubScheme": 0, "LoanCode": 0, "LoanName": "Not Available",
                                   "RoadWise": "true", "LevelCode": 1},
              "path": [], "leaf": "StateWiseListOfWorksSubReport", "title": "Proposals: State-wise List of Roads (road wise)"},
    "sanctioned": {"layout": "/SanctionsArea/Sanction/SanctionProjectLayout", "report": "/SanctionsArea/Sanction/SanctionProjectReport/",
                   "fields": lambda p: {**_place(p), "Year": 0, "Batch": 0, "BatchName": "All Batches", "Scheme": p["scheme"],
                                        "LevelCode": 1},
                   "path": ["each", "each"], "leaf": "SanctionedProjectsSubreport", "title": "Proposals: Sanctioned Projects"},
    "habitations": {"layout": "/CoreNetworkArea/CoreNetwork/CNHABLayout", "report": "/CoreNetworkArea/CoreNetwork/CNHABReport/",
                    "fields": lambda p: {**_place(p), "Route": 0, "RouteName": "All", "LevelCode": 1},
                    "path": ["each"], "leaf": "CN_HABLVL_Report", "title": "Core Network: Habitation-wise Core Network"},
    "facilities": {"layout": "/CoreNetworkArea/CoreNetwork/FacilityForCitizenLayout",
                   "report": "/CoreNetworkArea/CoreNetwork/FacilityForCitizenReport/",
                   "fields": lambda p: {**_place(p), "Route": 0, "RouteName": "All", "LevelCode": 1},
                   "path": [], "leaf": "FacilityDetailsForCitizen", "title": "Other Reports: Facility Details"},
    "monitors": {"layout": "/QualityMonitoringArea/QualityMonitoring/MonitorwiseGradingAbstract{qm}",
                 "report": "/QualityMonitoringArea/QualityMonitoring/MonitorwiseGradingAbstract{qm}Report/",
                 "fields": lambda p: {**_period({"from_year": p["year"], "to_year": p["year"], "to_month": 12}),
                                      "AdminQmCode": 0, "QMName": "All Monitors", "Scheme": 0,
                                      **({"StateCode": 0} if p["qm"] == "SQM" else {})},
                 "path": [], "leaf": "QM_Abs_Gr_Monitorwise_Report", "title": "Quality Monitoring: Monitor-wise Grading Abstract"},
    "qmworks": {"layout": "/QualityMonitoringArea/QualityMonitoring/QMMonWorksDetailLayout",
                "report": "/QualityMonitoringArea/QualityMonitoring/QMMonWorksDetailReport/",
                "fields": lambda p: {**_place(p), **_period(p), "LevelCode": 1},
                "path": [], "leaf": "NQM_SQM_Mon_Works_Detail_Report", "title": "Quality Monitoring: Works-wise Grading Abstract"},
}


def _where(p):
    return "-".join(str(x) for x in (p.get("state"), p.get("district"), p.get("scheme"), p.get("qm"), p.get("year"))
                    if x not in (None, ""))


def _base(p, kind, title, head, path):
    return {"kind": f"pmgsy_{kind}", **({"state_code": p["state"], "state": p["state_name"]} if p.get("state") else {}),
            **({"district_code": p["district"], "district": p.get("district_name")} if p.get("district") else {}),
            **({"scheme": p["scheme"]} if p.get("scheme") else {}), **({"qm": p["qm"]} if p.get("qm") else {}),
            **({"year": p["year"]} if p.get("year") else {}),
            "report_heading": head, "source": f"{SITE}/Home/CitizenPage ({title})", "csv_path": path}


def _store_rows(ctx, kind, p, spec, v, k, day):
    data = export(ctx, v)
    if data is None:
        return None
    text = data.decode("utf-8-sig", "replace")
    rows, head = parse_csv(text), heading(text)
    stored = ctx.hub.upload_many([(f"docs/{kind}/{_where(p)}-{k:03d}-{day}.csv", data, "text/csv")])[0]
    base = _base(p, kind, spec["title"], head, stored.get("path"))
    for r in rows:
        ident = [f"{a}={b}" for a, b in sorted(r.items()) if not a.startswith("textbox")]
        ctx.record(f"{kind}-{_where(p)}-{sha1(*ident)[:16]}", {**base, **r})
    return len(rows)


def report(ctx, p):
    kind = p["report"]
    spec = dict(REPORTS[kind])
    if kind == "monitors":
        spec["layout"], spec["report"] = spec["layout"].format(qm=p["qm"]), spec["report"].format(qm=p["qm"])
    leaves = walk(ctx, spec, spec["fields"](p))
    if leaves is None:
        return None
    day, n = time.strftime("%Y%m%d"), 0
    for k, (prefix, v) in enumerate(leaves):
        name = leaf_name(v)
        if name != spec["leaf"]:
            raise ValueError(f"{kind}: reached report {name}, expected {spec['leaf']} (path {prefix})")
        m = _store_rows(ctx, kind, p, spec, v, k, day)
        if m is None:
            return None
        n += m
    return n


# ---------------------------------------------------------------- quality monitor inspections

QM = SITE + "/QualityMonitoringArea/QualityMonitoring"


def qm(ctx, p):
    """Works-wise Grading Abstract of a district (records per work), then every NQM / SQM inspection of every work listed."""
    spec = REPORTS["qmworks"]
    v = viewer(ctx, spec["layout"], spec["report"], spec["fields"](p))
    if v is None:
        return None
    if leaf_name(v) != spec["leaf"]:
        raise ValueError(f"qm: reached report {leaf_name(v)}, expected {spec['leaf']}")
    day = time.strftime("%Y%m%d")
    n = _store_rows(ctx, "qmworks", p, spec, v, 0, day)
    if n is None:
        return None
    links = inspection_links(v)
    raw, misses, seen = [], 0, set()
    for code, typ in links:
        page = ctx.request("GET", f"{QM}/InspectionGradingDetails?id={code}${typ}", timeout=120)
        if page is None:
            misses += 1
            if misses > 20 and misses > len(raw) / 5:
                return None                               # the site stopped answering: the item is retried later
            continue
        dates, obs = inspection_list(page.text)
        ids = [o for o, _ in obs]
        if len(dates) > len(ids):                         # the list shows one date's observations: ask for every date
            for d, _ in dates:
                js = ctx.get_json("POST", f"{QM}/GetChainage/", timeout=60,
                                  data={"prRoadCode": code, "qmType": typ, "inspDate": d, "value": "0.5"})
                ids += [str(x.get("Value")) for x in js or [] if str(x.get("Value")) not in ids]
        for ob in ids:
            if (ob, typ) in seen:
                continue
            seen.add((ob, typ))
            r = ctx.request("GET", f"{QM}/InspDetails/{ob}${typ}", timeout=120, headers={"X-Requested-With": "XMLHttpRequest"})
            if r is None:
                misses += 1
                if misses > 20 and misses > len(raw) / 5:
                    return None
                continue
            raw.append({"road_code": code, "qm_type": typ, "observation": ob, "html": r.text})
            d = parse_inspection(r.text)
            h = d["head"]
            rec = {"kind": "pmgsy_qm_inspection", "state_code": p["state"], "district_code": p.get("district"),
                   "road_code": code, "qm_type": {"S": "SQM", "I": "NQM"}[typ], "observation_id": ob,
                   "monitor": h.get("Monitor Name"), "inspection_date": h.get("Inspection Date"), "state": h.get("State"),
                   "district": h.get("District"), "block": h.get("Block"), "package": h.get("Package"),
                   "sanction_year": h.get("Sanction Year"), "road_name": h.get("Road Name"), "work_type": h.get("Type"),
                   "status": h.get("Status"), "head": h, "items": d["items"],
                   "overall_grade": next((i["grade"] for i in d["items"] if i["item"].lower().startswith("overall")), None),
                   "photos": d["photos"], "points": d["points"],
                   "source": f"{QM}/InspectionGradingDetails?id={code}${typ}"}
            if PHOTOS and d["photos"]:
                rec["photo_paths"] = _photos(ctx, typ, ob, d["photos"])
            ctx.record(f"insp-{typ}-{ob}", rec)
            n += 1
    if raw:
        blob = gzip.compress("\n".join(json.dumps(x, ensure_ascii=False) for x in raw).encode())
        ctx.hub.upload_many([(f"raw/qm/{_where(p)}-{day}.jsonl.gz", blob, "application/gzip")])
    if misses:
        ctx.hub.failure("item", f"qm-{_where(p)}", f"{misses} inspection pages did not answer (kept the rest)")
    return n


def _photos(ctx, typ, ob, photos):
    out = []
    for k, ph in enumerate(photos):
        r = ctx.request("GET", ph["url"], timeout=120) if ph.get("url") else None
        if r is None or not r.content:
            out.append(None)
            continue
        ext = (ph["url"].rsplit(".", 1)[-1].lower() if "." in ph["url"][-6:] else "jpg")[:4]
        st = ctx.hub.upload_many([(f"photos/{typ}/{ob}/{k:02d}.{ext}", r.content, "image/jpeg")])[0]
        out.append(st.get("path"))
    return out


# ---------------------------------------------------------------- final bill payment

FB_LAYOUT, FB_REPORT = "/AccountArea/Account/FinalBillPaymentAbstractLayout", "/AccountArea/Account/FinalBillPaymentAbstractReport/"


def finalbill(ctx, p):
    """Every drillable cell of one agency's row in the Final Bill Payment Abstract, drilled through to its works."""
    fields = {"FundingAgency": 0, "PmgsyScheme": 0, "LevelIdStateAccount": 1}
    top = viewer(ctx, FB_LAYOUT, FB_REPORT, fields)
    if top is None:
        return None
    cells = [c for c in finalbill_cells(top) if c[0] == p["agency"]]
    day, n = time.strftime("%Y%m%d"), 0
    for k, (agency, period, col, act) in enumerate(cells):
        v = top if k == 0 else viewer(ctx, FB_LAYOUT, FB_REPORT, fields)
        v = drill(ctx, v, act) if v is not None else None
        if v is None:
            return None
        if leaf_name(v) != "FinalBillPaymentAbstractDrilldown":
            raise ValueError(f"finalbill: reached {leaf_name(v)}")
        data = export(ctx, v)
        if data is None:
            return None
        rows = parse_csv(data.decode("utf-8-sig", "replace"))
        slug = re.sub(r"[^A-Za-z0-9]+", "_", agency).strip("_")
        stored = ctx.hub.upload_many([(f"docs/finalbill/{slug}-{period}-{col}-{day}.csv", data, "text/csv")])[0]
        for r in rows:
            ident = [f"{a}={b}" for a, b in sorted(r.items()) if not a.startswith("textbox")]
            ctx.record(f"fb-{col}-{sha1(agency, period, *ident)[:16]}",
                       {"kind": "pmgsy_final_bill", "agency": agency, "period": period, "column": col,
                        "column_meaning": {"I": "physically complete, financial completion pending",
                                           "II": "physically complete, financial completion pending more than 6 months",
                                           "III": "physically completed works"}[col], **r,
                        "source": f"{SITE}/Home/CitizenPage (Financial Progress: Final Bill Payment Abstract)",
                        "csv_path": stored.get("path")})
        n += len(rows)
    return n


# ---------------------------------------------------------------- joint inspections with public representatives

def ji(ctx, p):
    y = p["year"]
    fields = {"MAST_STATE_CODE": 0, "PublicRepresentativeCode": "A", "FROM_MONTH": 1, "FROM_YEAR": y, "TO_MONTH": 12, "TO_YEAR": y}
    page = ctx.request("GET", QM + "/QMJIReportLayout", timeout=60, headers={"X-Requested-With": "XMLHttpRequest"})
    tok = token(page.text if page is not None else "")
    if not tok:
        return None
    rep = ctx.request("POST", QM + "/QMJIReport/", timeout=600, data={**fields, "__RequestVerificationToken": tok},
                      headers={"X-Requested-With": "XMLHttpRequest", "Referer": SITE + "/Home/CitizenPage"})
    src = iframe_src(rep.text if rep is not None else "")
    v = ctx.request("GET", src, timeout=LEAF_TIMEOUT) if src else None
    if v is None:
        return None
    v = v.text
    data = export(ctx, v)
    day = time.strftime("%Y%m%d")
    stored = ctx.hub.upload_many([(f"docs/ji/{y}-{day}.csv", data, "text/csv")])[0] if data else {}
    n = 0
    for cells in table_rows(v):
        link = next((a for _, _, a in cells if a and "QMJIDownloadPdf" in a), None)
        texts = [t for t, _, _ in cells if t]
        if not link or not texts or not texts[0].isdigit():
            continue
        fid = re.search(r"id=(\d+)", link).group(1)
        keys = ["sr", "state", "district", "block", "sanction_year", "package", "work_name", "inspection_date", "grading"]
        rec = {"kind": "pmgsy_joint_inspection", "year": y, **dict(zip(keys, texts)), "pdf_id": fid, "pdf_url": link,
               "source": f"{SITE}/Home/CitizenPage (Quality Monitoring: Joint Inspection with Public Representatives)",
               "csv_path": stored.get("path")}
        r = ctx.request("GET", link, timeout=120)
        if r is not None and r.content[:4] == b"%PDF":
            rec["pdf_path"] = ctx.hub.upload_many([(f"docs/ji/pdf/{fid}.pdf", r.content, "application/pdf")])[0].get("path")
            rec["pdf_bytes"] = len(r.content)
        ctx.record(f"ji-{fid}", rec)
        n += 1
    return n


def run(ctx, p):
    if not start_session(ctx):
        return None
    kind = p["report"]
    if kind == "qm":
        return qm(ctx, p)
    if kind == "finalbill":
        return finalbill(ctx, p)
    if kind == "ji":
        return ji(ctx, p)
    return report(ctx, p)
