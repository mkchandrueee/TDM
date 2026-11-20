import pytest
from sqlglot.errors import ParseError
from tigr.ssd_sql import inspect_sql


@pytest.mark.parametrize(
    "sql,params,operation",
    [
        ("SELECT * FROM public.accounts WHERE id=:id", {"id": 1}, "select"),
        ("WITH a AS (SELECT id FROM public.accounts) SELECT * FROM a", {}, "select"),
        ("SELECT a.id FROM public.accounts a JOIN public.users b ON a.id=b.id", {}, "select"),
        ("INSERT INTO public.accounts (id) VALUES (:id)", {"id": 1}, "insert"),
        ("UPDATE public.accounts SET name=:name WHERE id=:id", {"name": "x", "id": 1}, "update"),
        ("DELETE FROM public.accounts WHERE id=:id", {"id": 1}, "delete"),
    ],
)
def test_supported_statements(sql, params, operation):
    info = inspect_sql(sql, params)
    assert info["operation"] == operation
    assert ["public", "accounts"] in info["objects"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM public.accounts; DELETE FROM public.accounts",
        "DROP TABLE public.accounts",
        "TRUNCATE public.accounts",
        "WITH x AS (DELETE FROM public.accounts RETURNING *) SELECT * FROM x",
        "SELECT pg_sleep(10) FROM public.accounts",
        "SELECT public.lower(name) FROM public.accounts",
        "SELECT * INTO public.copy FROM public.accounts",
        "SELECT * FROM public.accounts FOR UPDATE",
        "INSERT INTO public.accounts(id) VALUES(1) ON CONFLICT DO NOTHING",
        "SELECT * FROM accounts",
        "SELECT id::public.evil FROM public.accounts",
        "SELECT 1 OPERATOR(public.+) 2 FROM public.accounts",
        "SELECT * FROM pg_authid WHERE EXISTS (SELECT 1 FROM (WITH pg_authid AS (SELECT * FROM public.accounts) SELECT * FROM pg_authid) a)",
        "SELECT * FROM pg_catalog.pg_authid",
        "CALL public.mutate()",
        "COPY public.accounts TO '/tmp/leak'",
    ],
)
def test_unsafe_sql_fails_closed(sql):
    with pytest.raises((ValueError, ParseError)):
        inspect_sql(sql, {})


def test_insert_select_and_returning_need_select():
    assert inspect_sql("INSERT INTO public.accounts(id) SELECT id FROM public.users", {})[
        "operations"
    ] == ["insert", "select"]
    assert inspect_sql("DELETE FROM public.accounts RETURNING id", {})["operations"] == [
        "delete",
        "select",
    ]


def test_parameters_cannot_be_added_or_omitted():
    with pytest.raises(ValueError):
        inspect_sql("SELECT * FROM public.accounts WHERE id=:id", {})


def test_parser_fallback_logs_do_not_contain_sql_literals(caplog):
    with caplog.at_level("WARNING", logger="sqlglot"), pytest.raises((ValueError, ParseError)):
        inspect_sql("CALL public.dangerous('source-private-token')", {})
    assert "source-private-token" not in caplog.text
