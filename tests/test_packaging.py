import unittest
from importlib.resources import files
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


class PackagingTests(unittest.TestCase):
    def test_runtime_and_workflow_dependencies_are_declared(self):
        project = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        runtime = tuple(project["dependencies"])
        self.assertTrue(any(value.startswith("matplotlib") for value in runtime))
        self.assertTrue(any(value.startswith("plotly") for value in runtime))
        extras = project["optional-dependencies"]
        self.assertTrue(any(value.startswith("numpy") for value in extras["examples"]))
        self.assertTrue(any(value.startswith("pytest") for value in extras["test"]))
        self.assertTrue(any(value.startswith("build") for value in extras["release"]))
        self.assertTrue(any(value.startswith("twine") for value in extras["release"]))

    def test_public_typing_marker_is_installed_as_package_data(self):
        self.assertTrue(files("sigvue").joinpath("py.typed").is_file())

    def test_standalone_build_support_is_installed_as_package_data(self):
        resources = files("sigvue._packaging")
        self.assertTrue(resources.joinpath("sigvue.spec").is_file())


if __name__ == "__main__":
    unittest.main()
