import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.checkout_offer_link import extract_checkout_code, link_offer_to_checkout


def test_extract_buygoods():
    url = (
        "https://buygoods.com/secure/checkout.html?account_id=13985"
        "&product_codename=hncg_front_6b_fnn1&redirect=aHR0cHM6Ly9ob25leWNvZy5jb20vdXAxLTZi"
    )
    assert extract_checkout_code(url, "Buygoods") == "hncg_front_6b_fnn1"


def test_extract_digistore():
    assert extract_checkout_code("https://www.checkout-ds24.com/product/753052", "Digistore24") == "753052"
    assert extract_checkout_code("https://www.checkout-ds24.com/product/555631/?cid=x", "digistore") == "555631"


def test_extract_jvzoo():
    assert extract_checkout_code("https://www.jvzoo.com/b/118423/453105/99", "JVZoo") == "453105"


def test_extract_pagamerican():
    assert extract_checkout_code("https://pay.pagamerican.app/HXR9DDrj", "PagAmerican") == "HXR9DDrj"


def test_extract_unknown_or_malformed():
    assert extract_checkout_code("https://a3-offers.mycartpanda.com/checkout/1:1", "CartPanda") is None
    assert extract_checkout_code("https://buygoods.com/secure/checkout.html", "Buygoods") is None


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.filters = db, table, None, {}

    def select(self, *_):
        self.op = "select"
        return self

    def update(self, values):
        self.op, self.values = "update", values
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def ilike(self, col, val):
        self.filters[col] = val
        return self

    async def execute(self):
        rows = [
            r for r in self.db.rows
            if all(str(r.get(c, "")).lower() == str(v).lower() for c, v in self.filters.items())
        ]
        if self.op == "update":
            for r in rows:
                r.update(self.values)
        return _Resp([dict(r) for r in rows])


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        return _Query(self, name)


def _rows():
    return [
        {"id": "c1", "checkout_code": "hncg_front_6b_fnn1", "network_id": "bg", "funnel_stage": "Purchase"},
        {"id": "c2", "checkout_code": "hncg_up1_6b_fnn1", "network_id": "bg", "funnel_stage": "Up1"},
    ]


def _link(db: Any, *args, **kwargs):
    return asyncio.run(link_offer_to_checkout(db, *args, **kwargs))


URL = "https://buygoods.com/secure/checkout.html?account_id=1&product_codename=hncg_front_6b_fnn1"


def test_link_by_checkout_id():
    db = _DB(_rows())
    got = _link(db, "rt1", "Buygoods", URL, "bg", checkout_id="c1")
    assert got == "c1"
    assert db.rows[0]["id_rt_offer"] == "rt1"


def test_link_by_checkout_id_not_found():
    db = _DB(_rows())
    assert _link(db, "rt1", "Buygoods", URL, "bg", checkout_id="nope") is None
    assert all("id_rt_offer" not in r for r in db.rows)


def test_link_fallback_by_code():
    db = _DB(_rows())
    assert _link(db, "rt1", "Buygoods", URL, "bg") == "c1"
    assert db.rows[0]["id_rt_offer"] == "rt1"
    assert "id_rt_offer" not in db.rows[1]


def test_link_fallback_ambiguous_does_nothing():
    rows = _rows() + [
        {"id": "c3", "checkout_code": "hncg_front_6b_fnn1", "network_id": "bg", "funnel_stage": "Purchase"}
    ]
    db = _DB(rows)
    assert _link(db, "rt1", "Buygoods", URL, "bg") is None
    assert all("id_rt_offer" not in r for r in db.rows)


def test_link_never_raises():
    class _Broken:
        def table(self, _):
            raise RuntimeError("boom")

    assert _link(_Broken(), "rt1", "Buygoods", URL, "bg", checkout_id="c1") is None
