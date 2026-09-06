"""Exercise real WebAssembly Python against a static-only HTTP server."""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import quote
from zipfile import ZipFile

import pytest


SITE = os.environ.get("SIGVUE_STATIC_SITE")
pytestmark = pytest.mark.skipif(not SITE, reason="Set SIGVUE_STATIC_SITE to a built static site")


@pytest.fixture(scope="module")
def browser_page():
    playwright = pytest.importorskip("playwright.sync_api")
    site = Path(SITE).resolve()
    # Serving the parent deliberately tests GitHub/GitLab project-site prefixes.
    handler = partial(SimpleHTTPRequestHandler, directory=str(site.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with playwright.sync_playwright() as driver:
        executable = os.environ.get("SIGVUE_CHROMIUM")
        browser = driver.chromium.launch(
            executable_path=executable or None,
            args=["--no-sandbox"],
        )
        page = browser.new_page(accept_downloads=True)
        page.set_default_timeout(180000)
        base = f"http://127.0.0.1:{server.server_port}/{quote(site.name)}/"
        requests = []
        page.on("request", lambda request: requests.append(request.url))
        # Also reject worker requests to CDNs, APIs, and any origin outside this static server.
        page.context.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(base)
                           else route.abort())
        page.goto(base)
        page.evaluate("async () => { await window.sigvueStatic.ready; }")
        yield page, base, requests
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join()


def api(page, path, payload=None):
    return page.evaluate(
        """async ({path,payload}) => {
          const response = await window.sigvueStatic.fetch(path, payload == null ? {} :
            {method:'POST',body:JSON.stringify(payload)});
          const data = await response.json();
          if (!response.ok) throw new Error(JSON.stringify(data));
          return data;
        }""",
        {"path": path, "payload": payload},
    )


def recordings(page, workspace):
    root = f"/workspaces/{workspace}/items"
    listing = api(page, root)
    items = list(listing["items"])
    directories = list(listing["directories"])
    while directories:
        directory = directories.pop()
        query = "&".join(f"directory={quote(part, safe='')}" for part in directory["path"])
        listing = api(page, f"{root}?{query}")
        items.extend(listing["items"])
        directories.extend(listing["directories"])
    return items


def test_all_recordings_render_and_export_without_backend(browser_page, tmp_path):
    page, base, requests = browser_page
    catalog = api(page, "/workspaces")
    assert {item["id"] for item in catalog["workspaces"]} == {
        "synthetic-lte-waterfall", "synthetic-comms",
    }
    opened = []
    for workspace in catalog["workspaces"]:
        items = recordings(page, workspace["id"])
        assert len(items) == (2 if workspace["id"] == "synthetic-lte-waterfall" else 3)
        for item in items:
            path = f"/workspaces/{workspace['id']}/items/{quote(item['id'], safe='')}"
            data = api(page, path)
            assert data["page"]["rendered_views"]
            assert data["page"]["annotation"]["enabled"]
            assert data["page"]["export"]["enabled"]
            opened.append((workspace["id"], item["id"], path))
        # Both exporters are run by scipy/numpy inside WASM, not precomputed files.
        for format in ("json", "mat"):
            job = api(page, path + "/exports", {"scope": "buffer", "format": format})
            status = api(page, job["status_url"])
            assert status["status"] == "ready", status
            file = status["files"][0]
            with page.expect_download() as download:
                page.evaluate("file => window.sigvueStatic.download(file)", file)
            destination = tmp_path / file["name"]
            download.value.save_as(destination)
            assert destination.stat().st_size > 128
            if format == "json":
                exported = json.loads(destination.read_text())
                assert exported
            else:
                from scipy.io import loadmat
                assert loadmat(destination)
    workspace, item, _ = opened[0]
    page.goto(f"{base}#/workspace/{workspace}/item/{quote(item, safe='')}")
    page.wait_for_selector(".js-plotly-plot")
    page.reload()
    page.wait_for_selector(".js-plotly-plot")
    assert "/workspace/" in page.url
    page.locator("#theme-toggle").select_option("dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert all(url.startswith(base) for url in requests)


def test_annotations_survive_reload_and_processing_controls_work(browser_page):
    page, base, _ = browser_page
    workspace = "synthetic-comms"
    item = recordings(page, workspace)[0]
    path = f"/workspaces/{workspace}/items/{quote(item['id'], safe='')}"
    original = api(page, path)
    assert original["page"]["statistics"]["Window start"] == "0.000 ms"
    comment = "Browser-local persistent annotation"
    created = api(page, path + "/annotations", {
        "position_seconds": 0.001,
        "duration_seconds": 0.001,
        "values": {"comment": comment},
    })
    assert comment in json.dumps(created)
    page.goto(base)
    page.reload()
    page.evaluate("async () => { await window.sigvueStatic.ready; }")
    restored = api(page, path)
    assert comment in json.dumps(restored)
    # Recompute a different actual source window rather than reusing a screenshot.
    moved = api(page, path + "?__window_start_seconds=0.002&__window_end_seconds=0.006")
    assert moved["page"]["rendered_views"]
    assert moved["page"]["statistics"]["Window start"] == "2.000 ms"
    assert moved["page"]["statistics"]["Window width"] == "4.000 ms"


def test_local_file_import_new_workspace_matplotlib_and_backup(browser_page, tmp_path):
    page, base, _ = browser_page
    source = b"""
from pathlib import Path
from matplotlib.figure import Figure
from sigvue import Files, Workspace

def load(path):
    return [float(value) for value in path.read_text().split(',')]

def view(data, ui):
    gain = ui.number("gain", default=2.0, minimum=0.0)
    figure = Figure()
    figure.subplots().plot([value * gain for value in data])
    ui.stat("Scaled sum", sum(data) * gain)
    with ui.tab("Values"):
        ui.plot(figure, key="local-plot")

def create_workspace(config):
    return Workspace(identifier="local-values", name="Local values",
        description="Imported visitor files",
        reader=Files(Path(config["data_root"]), "*.samples", load), view=view)
"""
    page.locator("#local-files").click()
    page.locator("#local-file-input").set_input_files({
        "name": "local_demo.py", "mimeType": "text/plain", "buffer": source,
    })
    page.wait_for_function("document.querySelector('#local-file-status').textContent.startsWith('Saved 1')")
    page.locator("#local-directory").fill("uploads")
    page.locator("#local-file-input").set_input_files({
        "name": "values.samples", "mimeType": "text/plain", "buffer": b"1,2,3",
    })
    page.wait_for_function("document.querySelector('#local-file-status').textContent.startsWith('Saved 1')")
    page.locator("#local-close").click()
    api(page, "/workspaces", {
        "use": "local_demo:create_workspace",
        "path": "/project",
        "id": "local-values",
        "name": "Local values",
        "config": {"data_root": "/project/uploads"},
        "persist": True,
        "profile_path": "/project/examples/browser.toml",
    })
    item = recordings(page, "local-values")[0]
    path = f"/workspaces/local-values/items/{quote(item['id'], safe='')}"
    opened = api(page, path)
    assert opened["page"]["statistics"]["Scaled sum"] == 12
    assert opened["page"]["rendered_views"][0]["kind"] == "matplotlib"
    changed = api(page, path + "?gain=3")
    assert changed["page"]["statistics"]["Scaled sum"] == 18
    page.goto(base)
    page.reload()
    page.evaluate("async () => { await window.sigvueStatic.ready; }")
    assert "local-values" in {entry["id"] for entry in api(page, "/workspaces")["workspaces"]}
    page.locator("#local-files").click()
    with page.expect_download() as download:
        page.locator("#local-backup").click()
    backup = tmp_path / "backup.zip"
    download.value.save_as(backup)
    with ZipFile(backup) as archive:
        assert archive.read("uploads/values.samples") == b"1,2,3"
        assert archive.read("local_demo.py") == source
        assert any(name.endswith(".sigmf-data") for name in archive.namelist())
    page.locator("#local-close").click()


def test_errors_are_real_http_responses(browser_page):
    page, _, _ = browser_page
    result = page.evaluate("""async () => {
      const response = await window.sigvueStatic.fetch('/not-a-route');
      return {status:response.status, data:await response.json()};
    }""")
    assert result["status"] == 404
    assert result["data"]["error"] == "not_found"
