"""analytics.relations + knowledge.table_entities: dò quan hệ giữa các bảng bằng dữ liệu, nhóm khoá chung, SQL nối bảng."""
from types import SimpleNamespace

import duckdb
import pandas as pd

from analytics import relations as R
from knowledge import table_entities as TE


def _cols(tid, df, labels):
    meta = [{"name": c, "original": labels.get(c, c), "type": "integer" if pd.api.types.is_integer_dtype(df[c]) else "text"} for c in df.columns]
    return [R.column_profile(tid, df, c) for c in R.key_columns(df, meta)]


SITES = pd.DataFrame({"site": [f"STE{n:04d}" for n in range(860, 880)], "site_name": [f"Clinic {n}" for n in range(20)]})
VISITS = pd.DataFrame({"site_number": [860 + (i % 15) for i in range(60)], "visit": [(i % 4) + 1 for i in range(60)]})


def test_codes_match_after_keeping_only_digits_and_the_one_side_is_the_catalogue():
    f = R.best_link(_cols("visits", VISITS, {"site_number": "Site Number"}), _cols("sites", SITES, {"site": "Site #"}))
    assert f is not None
    assert (f.left.table_id, f.right.table_id) == ("visits", "sites")  # bên "một" (danh mục site) ở phải
    assert f.cardinality == "many_to_one" and f.matched == 15 and f.left_coverage == 1.0
    assert [t.op for t in R.K.CANDIDATE_TRANSFORMS[f.ri]] == ["number_part"]


def test_different_code_families_with_the_same_digits_do_not_link():
    ncc = pd.DataFrame({"ma_ncc": [f"NCC{n:03d}" for n in range(1, 11)]})
    sp = pd.DataFrame({"ma_sp": [f"SP{n:03d}" for n in range(1, 11)]})
    assert R.best_link(_cols("a", ncc, {"ma_ncc": "Mã NCC"}), _cols("b", sp, {"ma_sp": "Mã SP"})) is None


def test_small_numbers_shared_by_chance_do_not_link():
    a = pd.DataFrame({"visit": [1, 2, 3, 4, 5, 6, 7, 8] * 3, "x": list("abcdefgh") * 3})
    b = pd.DataFrame({"totals": [1, 2, 3, 4, 5, 6, 7, 9] * 2})
    assert R.best_link(_cols("a", a, {"visit": "Visit"}), _cols("b", b, {"totals": "Totals"})) is None


def _rel(lt, lc, ltf, rt, rc, rtf, conf=0.9, status="proposed"):
    return SimpleNamespace(left_table_id=lt, left_column=lc, left_transforms=ltf, right_table_id=rt, right_column=rc,
                           right_transforms=rtf, confidence=conf, status=status)


def test_a_key_group_ends_up_in_one_key_form():
    """3 bảng: A "STE0870" ~ B "870" (phần số), B ~ C "870" (trực tiếp) -> cả nhóm so theo phần số."""
    num = [{"op": "number_part", "args": []}]
    rels = [_rel("A", "site", num, "B", "site_no", []), _rel("B", "site_no", [], "C", "site", []),
            _rel("A", "status", [], "C", "status", [], conf=0.4)]
    groups, ts = R.key_groups([r for r in rels if R.usable(r)])
    assert groups == [[("A", "site"), ("B", "site_no"), ("C", "site")]]
    assert all([t.op for t in ts[n]] == ["number_part"] for n in groups[0])
    assert not R.usable(_rel("A", "x", [], "B", "y", [], conf=0.99, status="rejected"))


def test_join_hint_sql_runs_on_duckdb():
    """Biểu thức khoá gửi cho model viết SQL phải chạy được trên DuckDB và nối đúng."""
    tables = [SimpleNamespace(id="v", name="visits", columns=[{"name": "site_number", "type": "integer", "original": "Site Number"}]),
              SimpleNamespace(id="s", name="sites", columns=[{"name": "site", "type": "text", "original": "Site #"}])]
    num = [{"op": "number_part", "args": []}]
    rel = _rel("v", "site_number", num, "s", "site", num)
    session = SimpleNamespace(execute=lambda q: SimpleNamespace(scalars=lambda: [rel]))
    [g] = R.join_hints(session, tables)
    exprs = {t: e for t, _, _, e in g.members}
    con = duckdb.connect()
    con.register("visits", VISITS)
    con.register("sites", SITES)
    n = con.execute(f"SELECT COUNT(*) FROM visits JOIN sites ON {exprs['visits']} = {exprs['sites']}").fetchone()[0]
    assert n == 60 and "Site #" in g.to_prompt()


def test_record_finder_and_entity_type():
    f = TE.RecordFinder([("r1", "Site # là STE0870, Name là Clinic A"), ("r2", "Subject là 1070-5056, Site là 870")])
    assert f.find("STE0870") == ["r1"] and f.find("1070-5056") == ["r2"] and f.find("870") == ["r2"]
    assert TE.guess_type(["Site #", "Site Number"]) == "location"
    assert TE.guess_type(["Mã sản phẩm"]) == "product"
    assert TE.guess_type(["Subject"]) == "person"
    assert TE.guess_type(["Status"]) == "other"
