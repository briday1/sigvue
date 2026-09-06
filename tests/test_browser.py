import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sigvue import Batch, BatchDestination, BatchResult, CapabilityChoice, Exporter
from sigvue.core.workspace import Workspace
from sigvue.web.application import _INDEX_HTML, _PLOTLY_JS
from sigvue.web.browser import BrowserRuntime, _EventLoopExecutor
from tests.fixtures import IdentityAnalysis, MemorySource, PlotlyPresentation


BINARY = bytes(range(256)) + b"\x00\xff\r\n"
ITEM = "/workspaces/test-workspace/items/recording"


class BinaryExporter(Exporter):
    scopes = (CapabilityChoice("full", "Full"),)
    formats = (CapabilityChoice("bin", "Binary"),)

    def export(self, source_data, delivered_data, request, directory):
        target = directory / "résultat output.bin"
        target.write_bytes(BINARY)
        return target


class ArtifactBatch(Batch):
    item_actions = (CapabilityChoice("render", "Render"),)
    workspace_actions = (CapabilityChoice("render", "Render"),)

    def __init__(self, directory):
        self.directory = directory
        self.runs = 0

    def item_destination(self, resource, request):
        return BatchDestination(self.directory, ("gallery", "report.html"))

    def run_item(self, resource, source_data, request, directory):
        self.runs += 1
        gallery = directory / "gallery"
        gallery.mkdir(exist_ok=True)
        (gallery / "résultat.bin").write_bytes(BINARY)
        report = directory / "report.html"
        report.write_bytes(b"<h1>Report</h1>")
        asset = directory / "report.assets" / "tile.png"
        asset.parent.mkdir(exist_ok=True)
        asset.write_bytes(BINARY)
        return BatchResult((gallery, report), "Rendered", (asset,))

    def run_workspace(self, resources, open_resource, request, directory):
        return self.run_item(resources[0], open_resource(resources[0]), request, directory)


@pytest.fixture
def browser_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def runtime(tmp_path, monkeypatch, browser_loop):
    # Application exports use tempfile; keep all generated files with the test.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    profile = tmp_path / "browser.toml"
    profile.write_text(
        '[browser]\ntitle = "Browser <test>"\nsubtitle = "Offline"\n'
        '[[workspaces]]\nuse = "tests.fixtures:create_workspace"\n'
        f"path = {json.dumps(str(Path.cwd()))}\n",
        encoding="utf-8",
    )
    with patch("threading.Thread.start", side_effect=AssertionError("No threads")), patch(
        "socket.socket", side_effect=AssertionError("No sockets")
    ):
        instance = BrowserRuntime(profile)
        instance.app.register_workspace(
            Workspace._from_runtime_components(
                identifier="binary",
                name="Binary",
                description="Binary artifacts",
                source=MemorySource(),
                analysis=IdentityAnalysis(),
                presentation=PlotlyPresentation(),
                exporter=BinaryExporter(),
                batch=ArtifactBatch(tmp_path / "saved"),
            )
        )
        yield instance


def decoded(response):
    data = base64.b64decode(response["body"], validate=True)
    if "Content-Length" in response["headers"]:
        assert len(data) == int(response["headers"]["Content-Length"])
    return data


def payload(runtime, method, path, body=""):
    response = runtime.request(method, path, body)
    return response["status"], json.loads(decoded(response))


def test_profile_and_existing_routes(runtime):
    assert runtime.app.reload_workspaces is False
    assert payload(runtime, "GET", "/health") == (200, {"status": "ok"})
    status, workspaces = payload(runtime, "GET", "/workspaces")
    assert status == 200
    assert workspaces["title"] == "Browser <test>"
    assert {workspace["id"] for workspace in workspaces["workspaces"]} == {
        "test-workspace", "binary"
    }
    for route in ("/", "/workspace/test-workspace", "/results/job/example"):
        assert decoded(runtime.request("GET", route)) == _INDEX_HTML.replace(
            "__BROWSER_TITLE__", "Browser &lt;test&gt;"
        ).replace("__BROWSER_SUBTITLE__", "Offline").encode()
    assert decoded(runtime.request("GET", "/assets/plotly.min.js")) == _PLOTLY_JS.encode()
    status, listing = payload(runtime, "GET", "/workspaces/test-workspace/items?q=Recording")
    assert status == 200
    assert listing["items"][0]["id"] == "recording"
    status, item = payload(runtime, "GET", ITEM + "?gain=2")
    assert status == 200
    plot = next(view for view in item["page"]["rendered_views"] if view["name"] == "signal")
    assert plot["value"]["data"][0]["y"] == [2.0, 4.0, 6.0, 8.0]


def test_annotations_utf8_and_request_isolation(runtime):
    body = json.dumps(
        {"position_seconds": 0.5, "duration_seconds": 0.25, "values": {"comment": "測定 café"}},
        ensure_ascii=False,
    )
    status, annotation = payload(runtime, "POST", ITEM + "/annotations", body)
    assert status == 201
    assert annotation["comment"] == "測定 café"
    assert annotation["position_seconds"] == 0.5
    assert payload(runtime, "GET", "/health") == (200, {"status": "ok"})
    assert runtime.app.registry.get("test-workspace").annotator.entries[0].comment == "測定 café"


@pytest.mark.parametrize(
    "method,path,body,status,error",
    [
        ("GET", "/missing", "", 404, "not_found"),
        ("POST", "/missing", "{}", 404, "not_found"),
        ("GET", "/workspaces/missing/items", "", 404, "workspace_not_found"),
        ("GET", "/exports/missing", "", 404, "workspace_not_found"),
        ("GET", "/batches/missing", "", 404, "workspace_not_found"),
        ("GET", "/batch-browser/job/missing", "", 404, "workspace_not_found"),
        ("GET", "/batch-files/missing/file.bin", "", 404, "workspace_not_found"),
        ("POST", ITEM + "/annotations", "{", 400, "bad_request"),
        ("POST", ITEM + "/annotations", "[]", 400, "bad_request"),
        ("POST", ITEM + "/annotations", "null", 400, "bad_request"),
        ("POST", ITEM + "/annotations", '{"values":[]}', 400, "bad_request"),
        ("POST", ITEM + "/annotations", '{"control_values":[]}', 400, "bad_request"),
        ("POST", ITEM + "/exports", '{"control_values":[]}', 400, "bad_request"),
        ("POST", ITEM + "/batch", '{"action":"missing"}', 400, "bad_request"),
        ("POST", "/workspaces", '{"persist":"true"}', 400, "bad_request"),
    ],
)
def test_route_errors(runtime, method, path, body, status, error):
    result_status, result = payload(runtime, method, path, body)
    assert result_status == status
    assert result["error"] == error


def test_internal_error_and_http_parser_errors(runtime, monkeypatch):
    def fail(*args):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(runtime.app, "open_item", fail)
    assert payload(runtime, "GET", ITEM) == (
        500, {"error": "internal_error", "detail": "fixture failure"}
    )
    assert runtime.request("DELETE", "/health")["status"] == 501
    assert runtime.request("GET INVALID", "/health")["status"] == 400
    assert runtime.request("GET", "/" + "x" * 65536)["status"] == 414
    for method, path in (("GET\r\nInjected: yes", "/"), ("GET", "/\r\nInjected: yes")):
        with pytest.raises(ValueError, match="control characters"):
            runtime.request(method, path)


@pytest.mark.parametrize(
    "path,body,expected",
    [
        (ITEM, '{"scope":"buffer","format":"json"}',
         b'{"scope": "buffer", "data": [1.0, 2.0, 3.0, 4.0]}'),
        ("/workspaces/binary/items/recording", '{"scope":"full","format":"bin"}', BINARY),
    ],
)
def test_exports_are_byte_exact_and_cleaned_after_download(runtime, path, body, expected):
    status, job = payload(runtime, "POST", path + "/exports", body)
    assert status == 202
    status, result = payload(runtime, "GET", job["status_url"])
    assert status == 200 and result["status"] == "ready"
    response = runtime.request("GET", result["files"][0]["url"])
    assert response["status"] == 200
    assert response["headers"]["Content-Disposition"].startswith("attachment;")
    assert decoded(response) == expected
    assert payload(runtime, "GET", job["status_url"])[0] == 404


def test_export_errors_remain_job_statuses(runtime):
    _, job = payload(runtime, "POST", ITEM + "/exports", '{"scope":"missing","format":"json"}')
    assert payload(runtime, "GET", job["status_url"])[1]["status"] == "error"


@pytest.mark.parametrize("route", ["/workspaces/binary/items/recording/batch", "/workspaces/binary/batch"])
def test_batch_artifacts_and_browsing(runtime, route):
    status, job = payload(runtime, "POST", route, '{"action":"render"}')
    assert status == 202 and job["status"] == "ready"
    base = "/batches/" + job["id"]
    for suffix, expected in (
        ("/gallery/r%C3%A9sultat.bin", BINARY),
        ("/report.assets/tile.png", BINARY),
        ("/report.html", b"<h1>Report</h1>"),
    ):
        response = runtime.request("GET", base + suffix)
        assert response["status"] == 200
        assert decoded(response) == expected
    response = runtime.request("GET", base + "/report.html")
    assert response["headers"]["Content-Disposition"].startswith("inline;")
    assert response["headers"]["Content-Security-Policy"] == "sandbox allow-scripts"
    response = runtime.request("GET", base + "/report.html?download=1")
    assert response["headers"]["Content-Disposition"].startswith("attachment;")
    assert "Content-Security-Policy" not in response["headers"]
    assert payload(runtime, "GET", base + "/gallery/%2E%2E/%2E%2E/outside.bin")[0] == 404
    assert payload(runtime, "GET", "/batch-browser/job/" + job["id"])[0] == 200
    assert payload(runtime, "GET", "/batch-browser/job/" + job["id"] + "/gallery")[0] == 200
    assert payload(runtime, "GET", "/batches")[0] == 200
    if "/items/" in route:
        runtime.app._batch_latest.clear()
        _, listing = payload(runtime, "GET", "/workspaces/binary/items")
        action = listing["items"][0]["batch"]["actions"][0]
        saved_url = action["result_browser_url"].replace("/results/saved/", "/batch-browser/saved/")
        assert payload(runtime, "GET", saved_url)[0] == 200
        for artifact in action["files"]:
            if artifact["name"] == "report.html":
                assert decoded(runtime.request("GET", artifact["url"])) == b"<h1>Report</h1>"
            else:
                directory_url = artifact["browse_url"].replace("/results/", "/batch-browser/")
                assert payload(runtime, "GET", directory_url)[0] == 200
                assert decoded(runtime.request("GET", artifact["url"] + "/résultat.bin")) == BINARY


def test_failed_batch_and_missing_downloads(runtime, monkeypatch):
    batch = runtime.app.registry.get("binary").batch

    def fail(*args):
        raise RuntimeError("batch failure")

    monkeypatch.setattr(batch, "run_item", fail)
    status, job = payload(
        runtime, "POST", "/workspaces/binary/items/recording/batch", '{"action":"render"}'
    )
    assert status == 202
    assert job["status"] == "error"
    assert job["detail"] == "batch failure"
    assert payload(runtime, "GET", job["status_url"] + "/missing.bin")[0] == 404


def test_jobs_yield_without_threads_and_pending_batch_can_cancel(runtime, browser_loop):
    async def exercise():
        _, export = payload(runtime, "POST", ITEM + "/exports", '{"scope":"full","format":"json"}')
        assert payload(runtime, "GET", export["status_url"])[1]["status"] == "pending"
        assert payload(runtime, "GET", export["status_url"] + "/recording-full.json")[0] == 404
        _, batch = payload(
            runtime, "POST", "/workspaces/binary/items/recording/batch", '{"action":"render"}'
        )
        assert batch["status"] == "pending"
        _, cancelled = payload(runtime, "POST", batch["status_url"] + "/cancel")
        assert cancelled["status"] == "cancelled"
        assert payload(runtime, "GET", "/health")[0] == 200
        await runtime.drain()
        assert payload(runtime, "GET", export["status_url"])[1]["status"] == "ready"
        assert payload(runtime, "GET", batch["status_url"])[1]["status"] == "cancelled"
        assert runtime.app.registry.get("binary").batch.runs == 0
        _, next_batch = payload(
            runtime, "POST", "/workspaces/binary/batch", '{"action":"render"}'
        )
        assert next_batch["status"] == "pending"
        await asyncio.sleep(0)
        assert payload(runtime, "GET", next_batch["status_url"])[1]["status"] == "ready"

    browser_loop.run_until_complete(exercise())


def test_executor_future_results_exceptions_callbacks_and_shutdown():
    executor = _EventLoopExecutor()
    future = executor.submit(lambda left, right: left + right, 2, right=3)
    assert future.result() == 5
    callbacks = []
    future.add_done_callback(lambda completed: callbacks.append(completed.result()))
    assert callbacks == [5]

    def fail():
        raise ValueError("job failure")

    with pytest.raises(ValueError, match="job failure"):
        executor.submit(fail).result()

    async def exercise():
        future = executor.submit(lambda: 10)
        assert not future.done()
        executor.shutdown(wait=False, cancel_futures=True)
        await executor.drain()
        assert future.cancelled()

    asyncio.run(exercise())
    with pytest.raises(RuntimeError, match="shutdown"):
        executor.submit(lambda: None)
