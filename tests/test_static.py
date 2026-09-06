import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from sigvue.web import static
from sigvue.web.application import _INDEX_HTML


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    examples = root / "examples"
    examples.mkdir(parents=True)
    (examples / "__init__.py").write_text("")
    (examples / "workspace.py").write_text("value = 1\n")
    (examples / "browser.toml").write_text(
        '[browser]\ntitle = "Science & <plots>"\nsubtitle = "Local data"\n'
        '[[workspaces]]\nuse = "examples.workspace:create_workspace"\npath = ".."\n'
        '[workspaces.config]\ndata_root = "data"\n'
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    packages = {
        name: {"file_name": f"{name}.whl", "depends": []}
        for name in static._PACKAGES
    }
    packages["matplotlib"]["depends"] = ["numpy", "dependency"]
    packages["dependency"] = {"file_name": "dependency.whl", "depends": ["nested"]}
    packages["nested"] = {"file_name": "nested.whl", "depends": ["dependency"]}
    packages["ssl"] = {"file_name": "ssl.zip", "depends": []}
    packages["_ssl"] = {"file_name": "_ssl.whl", "depends": [], "package_type": "cpython_module"}
    packages["unneeded"] = {"file_name": "unneeded.whl", "depends": []}
    for name in static._RUNTIME_FILES:
        (runtime / name).write_bytes(name.encode())
    (runtime / "pyodide.asm.js").write_bytes(b"core module")
    for package in packages.values():
        (runtime / package["file_name"]).write_bytes(b"runtime package")
    (runtime / "pyodide-lock.json").write_text(json.dumps({"packages": packages}))
    return root, runtime, tmp_path / "site"


@pytest.fixture
def small_distributions(tmp_path):
    """Keep builder tests fast without relying on external package contents."""
    files = {}
    for name in (
        "plotly/__init__.py", "plotly/package_data/plotly.min.js",
        "plotly-7.0.0.dist-info/METADATA", "narwhals/__init__.py",
        "narwhals-2.0.0.dist-info/METADATA",
    ):
        path = tmp_path / "installed" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        files[name] = path
    with patch.object(static, "_distribution_files", return_value=files):
        yield files


def build(project, **kwargs):
    root, runtime, output = project
    arguments = dict(root=root, config="examples/browser.toml", includes=["examples"], pyodide=runtime, output=output)
    arguments.update(kwargs)
    return static.build_static(**arguments)


def test_asset_layout_and_shared_html(project, small_distributions):
    output = build(project)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["config"] == "examples/browser.toml"
    assert manifest["project"] == "project.zip"
    assert manifest["framework"] == "framework.zip"
    assert manifest["pythonPackages"] == "python-packages.zip"
    assert manifest["packages"] == [*static._PACKAGES, "_ssl", "ssl"]
    assert len(manifest["buildId"]) == 64
    for filename in (*static._CLIENT_FILES, "index.html"):
        assert (output / filename).is_file()
    assert (output / "runtime" / "dependency.whl").is_file()
    assert (output / "runtime" / "nested.whl").is_file()
    assert (output / "runtime" / "ssl.zip").is_file()
    assert (output / "runtime" / "_ssl.whl").is_file()
    assert not (output / "runtime" / "unneeded.whl").exists()
    for filename in static._RUNTIME_FILES:
        assert (output / "runtime" / filename).is_file()
    assert (output / "assets" / "plotly.min.js").read_bytes() == small_distributions["plotly/package_data/plotly.min.js"].read_bytes()
    html = (output / "index.html").read_text()
    assert "Science &amp; &lt;plots&gt;" in html
    assert "Local data" in html
    assert 'src="assets/plotly.min.js"' in html
    assert html.index('id="app"') < html.index('src="static-client.js"') < html.index("await window.sigvueStatic.ready;")
    assert "(async function () {" in html
    assert "})().catch(error => console.error(error));" in html
    assert "__BROWSER_TITLE__" in _INDEX_HTML
    assert 'src="/assets/plotly.min.js"' in _INDEX_HTML
    assert "await window.sigvueStatic.ready;" not in _INDEX_HTML
    with ZipFile(output / "framework.zip") as archive:
        assert "sigvue/web/application.py" in archive.namelist()
        assert "sigvue/py.typed" in archive.namelist()
        assert not any("__pycache__" in name for name in archive.namelist())
    with ZipFile(output / "python-packages.zip") as archive:
        assert set(archive.namelist()) == set(small_distributions)


def test_project_archive_is_explicit_and_excludes_private_files(project, small_distributions):
    root, _, _ = project
    (root / "unselected-secret.txt").write_text("not for publication")
    examples = root / "examples"
    for name in (
        ".env", ".private/key", "__pycache__/cached.pyc", "tests/test_local.py",
        "test/test_local.py", "test_local.py", ".git/config", "nested/.secret",
        "module.pyc", "node_modules/package/main.js",
    ):
        path = examples / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded")
    (examples / "linked.txt").symlink_to(root / "unselected-secret.txt")
    (examples / "linked-directory").symlink_to(root, target_is_directory=True)
    output = build(project)
    with ZipFile(output / "project.zip") as archive:
        assert set(archive.namelist()) == {
            "examples/browser.toml", "examples/__init__.py", "examples/workspace.py",
        }


def test_config_is_implicitly_included_and_paths_are_root_relative(project, small_distributions):
    output = build(project, includes=["examples/workspace.py"])
    with ZipFile(output / "project.zip") as archive:
        assert set(archive.namelist()) == {"examples/browser.toml", "examples/workspace.py"}


@pytest.mark.parametrize("key,value", [
    ("path", "/private/project"), ("path", "../../outside"),
    ("path", "C:/private/project"), ("path", "~/project"),
    ("data_root", "/private/data"), ("data_root", "../../outside"),
    ("directory", "../../../data"), ("input_file", "/private/input"),
])
def test_nonportable_profile_paths_are_rejected(project, key, value):
    root, _, output = project
    config = '[[workspaces]]\nuse = "examples.workspace:create_workspace"\n'
    if key != "path":
        config += "[workspaces.config]\n"
    (root / "examples" / "browser.toml").write_text(config + f'{key} = "{value}"\n')
    with pytest.raises(ValueError, match="portable"):
        build(project)
    assert not output.exists()


def test_external_and_symlink_includes_are_rejected(project):
    root, _, output = project
    outside = root.parent / "outside.txt"
    outside.write_text("private")
    for value in (outside, "../outside.txt"):
        with pytest.raises(ValueError, match="outside"):
            build(project, includes=[value])
    (root / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        build(project, includes=["linked.txt"])
    (root / "linked-directory").symlink_to(root / "examples", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build(project, includes=["linked-directory/workspace.py"])
    assert not output.exists()


def test_nested_profile_paths_and_symlinked_data_are_rejected(project):
    root, _, _ = project
    profile = root / "examples" / "browser.toml"
    profile.write_text(
        '[[workspaces]]\nuse = "examples.workspace:create_workspace"\n'
        '[workspaces.config]\nsources = [{directory = "../../private"}]\n'
    )
    with pytest.raises(ValueError, match="portable"):
        build(project)
    (root / "examples" / "data").symlink_to(root, target_is_directory=True)
    profile.write_text(
        '[[workspaces]]\nuse = "examples.workspace:create_workspace"\n'
        '[workspaces.config]\ndata_root = "data"\n'
    )
    with pytest.raises(ValueError, match="symlink"):
        build(project)


def test_output_is_never_deleted_or_recursively_included(project):
    root, _, output = project
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        build(project)
    assert marker.read_text() == "keep"
    with pytest.raises(ValueError, match="inside an included"):
        build(project, output=root / "examples" / "site")
    with pytest.raises(ValueError, match="explicit"):
        build(project, includes=[])


def test_runtime_requires_complete_local_dependency_closure(project):
    _, runtime, output = project
    (runtime / "nested.whl").unlink()
    with pytest.raises(ValueError, match="nested.whl"):
        build(project)
    assert not output.exists()


def test_runtime_supports_modern_esm_core_module(project, small_distributions):
    _, runtime, _ = project
    (runtime / "pyodide.asm.js").rename(runtime / "pyodide.asm.mjs")
    output = build(project)
    assert (output / "runtime" / "pyodide.asm.mjs").is_file()
    assert not (output / "runtime" / "pyodide.asm.js").exists()


def test_runtime_copies_required_esm_loader(project, small_distributions):
    _, runtime, _ = project
    (runtime / "pyodide.mjs").write_bytes(b"ES module loader")
    output = build(project)
    assert (output / "runtime" / "pyodide.mjs").read_bytes() == b"ES module loader"


def test_runtime_requires_module_worker_loader(project):
    _, runtime, output = project
    (runtime / "pyodide.mjs").unlink()
    with pytest.raises(ValueError, match="pyodide.mjs"):
        build(project)
    assert not output.exists()


def test_build_id_and_archives_are_deterministic(project, small_distributions):
    root, _, first = project
    build(project)
    second = first.with_name("second")
    build(project, output=second)
    for filename in ("manifest.json", "project.zip", "framework.zip", "python-packages.zip"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()
    (root / "examples" / "workspace.py").write_text("value = 2\n")
    third = first.with_name("third")
    build(project, output=third)
    assert json.loads((first / "manifest.json").read_text())["buildId"] != json.loads((third / "manifest.json").read_text())["buildId"]


def test_installed_distributions_preserve_metadata_but_not_scripts(tmp_path):
    paths = [
        "plotly/__init__.py", "plotly/package_data/plotly.min.js",
        "plotly-7.0.0.dist-info/licenses/LICENSE.txt",
        "plotly-7.0.0.dist-info/METADATA", "plotly-7.0.0.dist-info/direct_url.json",
        "plotly-7.0.0.dist-info/RECORD", "plotly/tests/test_example.py",
        "plotly/native.so", "plotly/native.so.1", "plotly/__pycache__/module.pyc", "bin/executable",
        "../../../bin/executable", "/absolute.py", "plotly/.private", "_plotly_utils/__init__.py",
    ]
    for name in paths:
        if name.startswith(("/", "..")):
            continue
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data")
    (tmp_path / "plotly" / "link.py").symlink_to(tmp_path / "_plotly_utils" / "__init__.py")
    (tmp_path / "plotly" / "linked").symlink_to(tmp_path / "_plotly_utils", target_is_directory=True)
    paths.extend(["plotly/link.py", "plotly/linked/__init__.py"])

    class Distribution:
        files = paths

        def locate_file(self, path):
            return tmp_path / path

    with patch.object(static.metadata, "distribution", return_value=Distribution()):
        result = static._distribution_files(["plotly"])
    assert set(result) == {
        "plotly/__init__.py", "plotly/package_data/plotly.min.js",
        "_plotly_utils/__init__.py",
        "plotly-7.0.0.dist-info/licenses/LICENSE.txt",
        "plotly-7.0.0.dist-info/METADATA",
    }


def test_static_command_and_assets_are_packaged():
    root = Path(__file__).resolve().parents[1]
    project = static.tomllib.loads((root / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["sigvue-static"] == "sigvue.web.static:main"
    assert project["project"]["optional-dependencies"]["static-test"] == ["playwright==1.58.0"]
    assert "static_assets/*.js" in project["tool"]["setuptools"]["package-data"]["sigvue.web"]
    for name in static._CLIENT_FILES:
        assert (Path(static.__file__).parent / "static_assets" / name).is_file()


def test_cli_requires_explicit_assets():
    with pytest.raises(SystemExit) as error:
        static.main(["--root", ".", "--config", "browser.toml", "--pyodide", "runtime", "--output", "site"])
    assert error.value.code == 2
