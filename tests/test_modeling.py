"""Mô hình dữ liệu (modeling/): ngôn ngữ công thức, dịch công thức Excel, chuẩn hoá khoá, đọc workbook + pivot,
suy luận quan hệ/logic từ dữ liệu khi giá trị đã bị "dán" (không còn công thức), biên dịch view và đối chiếu, tài liệu."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from modeling import expr as E
from modeling import keys as K
from modeling.compile import compile_view
from modeling.document import build_blocks, to_markdown
from modeling.evaluate import DuckEval, compare
from modeling.excel_formula import SheetMap, Translator
from modeling.explain import column_text, relationship_text
from modeling.infer import SourceFile, analyze
from modeling.spec import Condition, Rule, Transform
from modeling.workbook import read_workbook

AS_OF = date(2026, 6, 1)


# ===================================================================================== expr
def test_expr_parse_print_and_columns():
    node = E.parse("=DAYS(AS_OF(), [Last Visit]) + ROUND([A b] / 7, 1)")
    assert E.columns_used(node) == ["Last Visit", "A b"]
    assert E.uses_as_of(node)
    assert E.to_text(E.parse(E.to_text(node))) == E.to_text(node)


@pytest.mark.parametrize("bad", ["", "[a] +", "FOO([a])", "[a] [b]", "DAYS([a])", "SELECT 1"])
def test_expr_rejects_invalid(bad):
    with pytest.raises(E.ExprError):
        E.parse(bad)


def test_expr_sql_runs_in_duckdb():
    import duckdb

    types = {"d1": "date", "d2": "date", "n": "integer", "t": "text"}
    resolve = lambda name: E.SqlCol(f'"{name}"', types[name])  # noqa: E731
    sql = E.to_sql(E.parse("DAYS([d2], [d1]) + IF([n] > 2, 1, 0) + VALUE(DIGITS([t]))"), resolve, "DATE '2026-06-01'")
    con = duckdb.connect()
    con.execute("CREATE TABLE x (d1 DATE, d2 DATE, n BIGINT, t VARCHAR)")
    con.execute("INSERT INTO x VALUES (DATE '2026-01-01', DATE '2026-01-11', 3, 'STE0042')")
    assert con.execute(f"SELECT {sql} FROM x").fetchone()[0] == 10 + 1 + 42


# ===================================================================================== keys
@pytest.mark.parametrize("value,ts,expected", [
    ("STE0870", [Transform(op="number_part")], "870"),
    (870, [Transform(op="number_part")], "870"),
    ("8644-5686", [Transform(op="first_token")], "8644"),
    (" ab ", [], "AB"),
    ("PRJ-1", [Transform(op="remove_prefix", args=["prj-"])], "1"),
])
def test_key_transform_python_matches_sql(value, ts, expected):
    import duckdb

    assert K.apply_py(value, ts) == expected
    ctype = "integer" if isinstance(value, int) else "text"
    lit = str(value) if isinstance(value, int) else "'" + value + "'"
    got = duckdb.connect().execute(f"SELECT {K.to_sql('v', ctype, ts)} FROM (SELECT {lit} AS v)").fetchone()[0]
    assert got == expected


# ===================================================================================== excel formula
def _translator():
    sheets = {
        "Summary": SheetMap("summary", {1: ("site", "Site"), 2: ("region", "Region"), 4: ("last_visit", "Last Visit")}, 2, 50),
        "Issues": SheetMap("issues", {1: ("issue_id", "Issue"), 2: ("site", "Site"), 3: ("status", "Status"), 4: ("age", "Age")}, 2, 99),
        "Sites": SheetMap("sites", {1: ("site", "Site"), 2: ("city", "City"), 3: ("pi", "PI")}, 2, 20),
    }
    return Translator(sheets, "Summary", lambda s, r, c: date(2026, 6, 1) if (s, r, c) == ("Params", 1, 2) else None)


def test_translate_countifs_with_filters_and_join():
    t = _translator().translate('=COUNTIFS(Issues!$B:$B,$A2,Issues!$C:$C,"<>Closed",Issues!$D:$D,">="&30)', 2)
    assert t.error is None
    r = t.rule
    assert (r.kind, r.func, r.table, r.default) == ("aggregate", "count", "issues", 0)
    assert [(f.column, f.op, f.value) for f in r.filters] == [("status", "ne", "Closed"), ("age", "gte", 30)]
    assert [(j.base_column, j.source_table, j.source_column) for j in t.joins] == [("site", "issues", "site")]


def test_translate_vlookup_iferror_and_index_match():
    t = _translator().translate("=IFERROR(VLOOKUP($A2,Sites!$A:$C,3,FALSE),\"n/a\")", 2)
    assert (t.rule.kind, t.rule.table, t.rule.column, t.rule.default) == ("lookup", "sites", "pi", "n/a")
    t2 = _translator().translate("=INDEX(Sites!$B:$B,MATCH($A2,Sites!$A:$A,0))", 2)
    assert (t2.rule.kind, t2.rule.column) == ("lookup", "city")


def test_translate_row_formula_and_today():
    t = _translator().translate("=TODAY()-D2", 2)
    assert t.rule.kind == "formula" and t.rule.expression == "AS_OF() - [Last Visit]"
    t2 = _translator().translate("=Params!$B$1-D2", 2)  # ô tham số -> hằng ngày
    assert t2.rule.expression == "DATE(2026, 6, 1) - [Last Visit]"
    assert _translator().translate("=TRUE()", 2).rule.kind == "constant"


def test_translate_unsupported_reports_error():
    t = _translator().translate("=SUMPRODUCT((Issues!B:B=A2)*(Issues!D:D))", 2)
    assert t.rule is None and t.error


# ===================================================================================== workbook end-to-end
def _data(seed: int = 7):
    rnd = random.Random(seed)
    sites = [f"STE{n:04d}" for n in range(1, 41)]
    site_rows = [(s, rnd.choice(["North", "South"]), f"Dr {i}") for i, s in enumerate(sites)]
    issues = []
    for i in range(400):
        s = rnd.choice(sites)
        issues.append((f"I{i}", s, rnd.choice(["Open", "Closed", "Pending"]), rnd.randint(0, 120)))
    visits = []
    for i in range(300):
        s = rnd.choice(sites[:35])
        visits.append((int(s[3:]), rnd.choice(["IMV", "SMC"]), datetime(2025, 1, 1) + timedelta(days=rnd.randint(0, 480)), round(rnd.uniform(0.5, 3), 2)))
    return sites, site_rows, issues, visits


def _write(path, *, formulas: bool):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    sites, site_rows, issues, visits = _data()
    wb = Workbook()
    ws = wb.active
    ws.title = "Site Summary"
    ws.merge_cells("C1:E1")
    ws["C1"] = "Monitoring"
    ws.append(["Site #", "Region", "Open Issues", "Last IMV", "Days since last IMV", "Hours on site", "PI"])
    for c in ws[2]:
        c.font = Font(bold=True)
    for r, (s, region, pi) in enumerate(site_rows, start=3):
        if formulas:
            ws.append([s, region, f'=COUNTIFS(Issues!$B:$B,$A{r},Issues!$C:$C,"<>Closed")',
                       f'=MAXIFS(Visits!$C:$C,Visits!$A:$A,VALUE(MID($A{r},4,10)),Visits!$B:$B,"IMV")', f"=TODAY()-D{r}",
                       f"=SUMIFS(Visits!$D:$D,Visits!$A:$A,VALUE(MID($A{r},4,10)))", f"=VLOOKUP($A{r},Sites!$A:$C,3,FALSE)"])
        else:
            n = int(s[3:])
            open_ = sum(1 for i in issues if i[1] == s and i[2] != "Closed")
            imv = [v[2] for v in visits if v[0] == n and v[1] == "IMV"]
            last = max(imv) if imv else None
            hours = round(sum(v[3] for v in visits if v[0] == n), 2)
            ws.append([s, region, open_, last, (AS_OF - last.date()).days if last else None, hours, pi])
    w2 = wb.create_sheet("Issues")
    w2.append(["Issue", "Site", "Status", "Age"])
    for row in issues:
        w2.append(list(row))
    w3 = wb.create_sheet("Visits")
    w3.append(["Site No", "Visit Type", "Visit Date", "Hours"])
    for row in visits:
        w3.append(list(row))
    w4 = wb.create_sheet("Sites")
    w4.append(["Site", "Region", "PI"])
    for row in site_rows:
        w4.append(list(row))
    wb.save(path)


@pytest.fixture(scope="module")
def pasted(tmp_path_factory):
    p = tmp_path_factory.mktemp("wb") / "pasted.xlsx"
    _write(p, formulas=False)
    spec, frames = analyze([SourceFile("doc1", "pasted.xlsx", "Pasted", str(p), "xlsx")])
    return spec, frames


def test_workbook_reader_groups_and_header(tmp_path):
    p = tmp_path / "f.xlsx"
    _write(p, formulas=True)
    wb = read_workbook(str(p))
    main = wb.tables[0]
    assert main.header_row == 2 and main.labels[:3] == ["Site #", "Region", "Open Issues"]
    assert main.groups[2:5] == ["Monitoring"] * 3
    pat, n, total = main.dominant_formula(2)
    assert pat.startswith("=COUNTIFS(") and n == total == 40


def test_formula_workbook_rules_read_from_excel(tmp_path):
    p = tmp_path / "f.xlsx"
    _write(p, formulas=True)
    spec, _ = analyze([SourceFile("d", "f.xlsx", "F", str(p), "xlsx")])
    assert spec.main_table == "site_summary"
    col = spec.column("open_issues")
    assert col.rule.kind == "aggregate" and col.rule.func == "count"
    assert [(f.column, f.op, f.value) for f in col.rule.filters] == [("status", "ne", "Closed")]
    assert col.evidence[0].kind == "formula"
    rel = spec.relationship(col.rule.relationship_id)
    assert rel.source_table == "issues" and rel.keys[0].base_column == "site"
    assert spec.column("days_since_last_imv").rule.expression == "AS_OF() - [Last IMV]"
    assert spec.column("pi").rule.kind == "lookup"
    assert any(n.code == "main_has_formulas" for n in spec.notices)


def test_pasted_values_logic_recovered_from_data(pasted):
    spec, frames = pasted
    assert spec.main_table == "site_summary"
    assert spec.parameters.as_of_date == AS_OF.isoformat()
    rels = {r.source_table: r for r in spec.relationships}
    visits = rels["visits"]
    assert visits.keys[0].base_column == "site" and visits.keys[0].base_transforms[0].op == "number_part"
    open_ = spec.column("open_issues")
    assert open_.rule.kind == "aggregate" and open_.rule.func == "count"
    assert [(f.column, f.op) for f in open_.rule.filters] == [("status", "ne")]
    assert open_.check.rate == 1.0
    last = spec.column("last_imv")
    assert (last.rule.kind, last.rule.func, last.rule.column) == ("aggregate", "max", "visit_date")
    assert last.rule.filters and last.rule.filters[0].value == "IMV"
    assert spec.column("hours_on_site").check.rate == 1.0
    assert spec.column("pi").rule.kind == "lookup" and spec.column("pi").check.rate == 1.0
    days = spec.column("days_since_last_imv")
    assert days.rule.kind == "formula" and days.check.rate == 1.0 and days.check.basis == "view"
    assert spec.column("site").rule.kind == "input" and spec.column("site").is_key


def test_view_reproduces_excel_and_texts(pasted):
    spec, frames = pasted
    ev = DuckEval(frames)
    checks, problems = ev.check_all(spec)
    assert not problems
    for name in ("open_issues", "last_imv", "days_since_last_imv", "hours_on_site", "pi"):
        assert checks[name].rate == 1.0, name
    t = column_text(spec, spec.column("open_issues"))
    assert "Issues" in t["en"] and "Closed" in t["vi"]
    assert "Visits" in relationship_text(spec, spec.relationship("r_visits"))["vi"]
    title, blocks = build_blocks(spec, name="Demo", schema="dm_x", validation=None, view_sql=compile_view(spec).sql, lang="vi")
    md = to_markdown(title, blocks)
    assert "Quan hệ giữa các sheet" in md and "Open Issues" in md and "CREATE VIEW dm_x." in md


def test_check_rule_and_compare_samples(pasted):
    spec, frames = pasted
    ev = DuckEval(frames)
    wrong = Rule(kind="aggregate", relationship_id="r_issues", table="issues", func="count", default=0)  # quên điều kiện
    chk, err = ev.check_rule(spec, "open_issues", wrong)
    assert err is None and chk.rate < 1.0 and chk.samples
    good = Rule(kind="aggregate", relationship_id="r_issues", table="issues", func="count", default=0,
                filters=[Condition(column="status", op="in", value=["Open", "Pending"])])
    chk2, _ = ev.check_rule(spec, "open_issues", good)
    assert chk2.rate == 1.0


def test_compare_numeric_rounding_and_text():
    c = compare(pd.Series([8.7, 1.0, None]), pd.Series([8.6999999, 1.0, 5]), "double")
    assert (c.compared, c.matched) == (2, 2)
    t = compare(pd.Series(["Dr A ", "x"]), pd.Series(["dr a", "y"]), "text")
    assert t.matched == 1 and t.samples[0]["expected"] == "x"
