"""Exercise real WebAssembly Python against a static-only HTTP server."""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import product
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
                           if route.request.url.startswith(base) and route.request.method == "GET"
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
        for scope, format in product(("buffer", "full"), ("json", "mat")):
            job = api(page, path + "/exports", {"scope": scope, "format": format})
            status = api(page, job["status_url"])
            assert status["status"] == "ready", status
            file = status["files"][0]
            with page.expect_download() as download:
                page.evaluate("file => window.sigvueStatic.download(file)", file)
            destination = tmp_path / file["name"]
            download.value.save_as(destination)
            assert destination.stat().st_size > 128
            import numpy as np
            from examples.formats.sigmf.recording import load_sigmf_recording
            with ZipFile(Path(SITE) / "project.zip") as archive:
                source = next(name for name in archive.namelist()
                              if name.endswith(f"/{item['id']}.sigmf-data"))
                metadata_source = source.replace(".sigmf-data", ".sigmf-meta")
                metadata_path = tmp_path / Path(metadata_source).name
                metadata_path.write_bytes(archive.read(metadata_source))
                (tmp_path / Path(source).name).write_bytes(archive.read(source))
            recording = load_sigmf_recording(metadata_path)
            expected = recording.read(0, recording.sample_count)
            if format == "json":
                exported = json.loads(destination.read_text())
                assert exported["scope"] == scope
                count = exported["sample_count"]
                actual = np.asarray(exported["samples"]["real"]) + 1j * np.asarray(exported["samples"]["imag"])
            else:
                from scipy.io import loadmat
                exported = loadmat(destination)
                count = int(exported["sample_count"][0, 0])
                actual = exported["samples"]
            assert count == (expected.shape[-1] if scope == "full" else round(0.012 * recording.sample_rate))
            np.testing.assert_array_equal(actual, expected[:, :count])
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
from sigvue import Batch, BatchDestination, BatchResult, CapabilityChoice, Files, Workspace

def load(path):
    return [float(value) for value in path.read_text().split(',')]

def view(data, ui):
    gain = ui.number("gain", default=2.0, minimum=0.0)
    figure = Figure()
    figure.subplots().plot([value * gain for value in data])
    ui.stat("Scaled sum", sum(data) * gain)
    with ui.tab("Values"):
        ui.plot(figure, key="local-plot")

class PlotBatch(Batch):
    @property
    def item_actions(self):
        return (CapabilityChoice("plot", "Render PNG"),)

    def item_destination(self, resource, request):
        return BatchDestination(Path("/project/results"), files=("plot.png",))

    def run_item(self, resource, source_data, request, directory):
        figure = Figure()
        figure.subplots().plot(source_data)
        path = directory / "plot.png"
        figure.savefig(path)
        return BatchResult((path,), "Rendered locally")

def create_workspace(config):
    return Workspace(identifier="local-values", name="Local values",
        description="Imported visitor files",
        reader=Files(Path(config["data_root"]), "*.samples", load), view=view,
        batch=PlotBatch())
"""
    page.locator("#local-files").click()
    page.locator("#local-file-input").set_input_files([
        {"name": "local_demo.py", "mimeType": "text/plain", "buffer": source},
        {"name": "pyproject.toml", "mimeType": "text/plain", "buffer": b"""
[project]
name = "browser-imports"
[project.entry-points."sigvue.workspaces"]
local-values = "local_demo:create_workspace"
"""},
    ])
    page.wait_for_function("document.querySelector('#local-file-status').textContent.startsWith('Saved 2')")
    page.locator("#local-directory").fill("uploads")
    page.locator("#local-file-input").set_input_files({
        "name": "values.samples", "mimeType": "text/plain", "buffer": b"1,2,3",
    })
    page.wait_for_function("document.querySelector('#local-file-status').textContent.startsWith('Saved 1')")
    page.locator("#local-close").click()
    page.locator("#workspace-add").click()
    option = page.locator("#workspace-factory option", has_text="local-values")
    option.wait_for(state="attached")
    page.locator("#workspace-factory").select_option(option.get_attribute("value"))
    page.locator("#workspace-data-root").fill("/project/uploads")
    page.locator("#workspace-name").fill("Local values")
    page.locator("#workspace-id").fill("local-values")
    page.locator("#workspace-persist").check()
    page.locator("#workspace-profile-path").fill("/project/examples/browser.toml")
    page.locator("#workspace-wizard-submit").click()
    page.locator("#workspace-wizard").wait_for(state="hidden")
    item = recordings(page, "local-values")[0]
    path = f"/workspaces/local-values/items/{quote(item['id'], safe='')}"
    opened = api(page, path)
    assert opened["page"]["statistics"]["Scaled sum"] == 12
    assert opened["page"]["rendered_views"][0]["kind"] == "matplotlib"
    changed = api(page, path + "?gain=3")
    assert changed["page"]["statistics"]["Scaled sum"] == 18
    job = api(page, path + "/batch", {"action": "plot"})
    status = api(page, job["status_url"])
    assert status["status"] == "ready", status
    dimensions = page.evaluate("""url => new Promise((resolve,reject) => {
      const image = new Image();
      image.onload = () => resolve([image.naturalWidth,image.naturalHeight]);
      image.onerror = () => reject(new Error('Local batch image did not load'));
      image.src = url;
    })""", status["files"][0]["url"])
    assert dimensions[0] > 0 and dimensions[1] > 0
    page.goto(status["result_browser_url"])
    page.wait_for_selector(".result-browser")
    saved = recordings(page, "local-values")[0]["batch"]["actions"][0]["collection_browser_url"]
    assert saved.startswith(base + "#/results/saved/")
    page.goto(saved)
    page.reload()
    page.wait_for_selector(".result-browser")
    page.wait_for_function("document.querySelector('.result-image-stage img')?.naturalWidth > 0")
    page.goto(base)
    page.reload()
    page.evaluate("async () => { await window.sigvueStatic.ready; }")
    assert "local-values" in {entry["id"] for entry in api(page, "/workspaces")["workspaces"]}
    next_manifest = json.loads((Path(SITE) / "manifest.json").read_text())
    next_manifest["buildId"] = "test-next-deployment"
    page.context.route("**/manifest.json", lambda route: route.fulfill(
        content_type="application/json", body=json.dumps(next_manifest),
    ))
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
        assert archive.read("results/plot.png").startswith(b"\x89PNG")
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
