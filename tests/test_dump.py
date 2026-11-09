"""Pump&Dump wurde abgeschaltet: die URLs leiten auf das Anlageformular um."""

from __future__ import annotations

from tests.conftest import post_form


def test_dump_get_redirects_to_watch_form(auth_client) -> None:
    r = auth_client.get("/dump", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/watches/new"


def test_dump_post_redirects_to_watch_form(auth_client) -> None:
    r = post_form(auth_client, "/dump", {"title": "Ignoriert"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/watches/new"


def test_dump_review_redirects_to_detail(auth_client) -> None:
    from tests.conftest import create_watch

    watch_id = create_watch(auth_client)
    r = auth_client.get(f"/dump/{watch_id}/review", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/watches/{watch_id}"


def test_dump_requires_auth(client) -> None:
    r = client.get("/dump", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]
