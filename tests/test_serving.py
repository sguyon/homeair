"""How the copies a home runs send files: gzip when asked, a year's browser cache for a versioned /static/ URL."""
import gzip
import re

import server

GZIP = {"Accept-Encoding": "gzip, deflate, br"}


def page_assets(client):
    return re.findall(r'(?:src|href)="(/static/[^"]+|/manifest\.json[^"]*)"', client.get("/").get_data(as_text=True))


def test_js_is_gzipped_when_asked(client):
    plain = client.get("/static/app.js").get_data()
    resp = client.get("/static/app.js", headers=GZIP)
    assert resp.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in resp.headers["Vary"]
    assert int(resp.headers["Content-Length"]) == len(resp.get_data()) < len(plain) / 2
    assert gzip.decompress(resp.get_data()) == plain
    again = client.get("/static/app.js", headers=GZIP)   # the kept gzipped copy
    assert again.get_data() == resp.get_data()


def test_no_gzip_unless_asked(client):
    resp = client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in resp.headers
    assert resp.get_data().startswith(b"//")
    assert "Content-Encoding" not in client.get("/static/app.js", headers={"Accept-Encoding": "gzip;q=0"}).headers


def test_page_and_json_are_gzipped_images_are_not(client):
    page = client.get("/", headers=GZIP)
    assert page.headers["Content-Encoding"] == "gzip" and b"<html" in gzip.decompress(page.get_data())
    assert "Content-Encoding" not in client.get("/static/og-demo.png", headers=GZIP).headers
    assert "Content-Encoding" not in client.get("/health", headers=GZIP).headers   # under 1 KB


def test_gzipped_file_still_answers_304(client):
    etag = client.get("/static/app.js", headers=GZIP).headers["ETag"]
    assert etag.startswith("W/")
    assert client.get("/static/app.js", headers={**GZIP, "If-None-Match": etag}).status_code == 304


def test_vercel_leaves_compression_to_its_edge(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert "Content-Encoding" not in client.get("/static/app.js", headers=GZIP).headers


def test_page_references_carry_a_version(client):
    assets = page_assets(client)
    assert any(a.startswith("/static/app.js?v=") for a in assets) and any(a.startswith("/static/map.js?v=") for a in assets)
    for a in assets:
        assert re.search(r"\?v=[0-9a-f]{10}$", a), a
        assert client.get(a).status_code == 200, a


def test_versioned_static_is_immutable(client):
    url = next(a for a in page_assets(client) if a.startswith("/static/app.js?v="))
    assert client.get(url).headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert client.get(url, headers=GZIP).headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_unversioned_or_stale_static_is_revalidated(client):
    assert client.get("/static/app.js").headers["Cache-Control"] == "no-cache"
    assert client.get("/static/app.js?v=0000000000").headers["Cache-Control"] == "no-cache"   # an old page's URL


def test_service_worker_and_manifest_are_never_immutable(client):
    for path in ("/sw.js", "/sw.js?v=1", "/manifest.json", "/manifest.json?v=" + server.asset_version("/manifest.json")):
        cc = client.get(path).headers.get("Cache-Control", "")
        assert "immutable" not in cc and "max-age=31536000" not in cc, path


def test_api_cache_headers_unchanged(client):
    assert client.get("/api/setup", headers=GZIP).headers["Cache-Control"] == "no-store"
    assert client.post("/api/token", headers=GZIP).headers["Cache-Control"] == "no-store"
    assert client.get("/api/context", headers=GZIP).headers["Cache-Control"] == "no-cache"


def test_asset_version_follows_the_file(tmp_path, monkeypatch):
    f = tmp_path / "static" / "x.js"
    f.parent.mkdir()
    f.write_text("a")
    monkeypatch.setattr(server, "__file__", str(tmp_path / "server.py"))
    first = server.asset_version("/static/x.js")
    assert server.asset_version("/static/x.js") == first
    f.write_text("bb")
    assert server.asset_version("/static/x.js") != first
