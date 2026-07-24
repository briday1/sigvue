import unittest
from importlib.resources import files
from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


class PackagingTests(unittest.TestCase):
    @staticmethod
    def dependency_names(requirements):
        return {
            re.split(r"[\s;<>=!~\[]", requirement, maxsplit=1)[0].lower()
            for requirement in requirements
        }

    def test_runtime_and_workflow_dependencies_are_declared(self):
        project = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        runtime = tuple(project["dependencies"])
        self.assertEqual(
            {
                "certifi",
                "matplotlib",
                "numpy",
                "pillow",
                "plotly",
                "tomli",
            },
            self.dependency_names(runtime),
        )
        extras = project["optional-dependencies"]
        self.assertEqual(
            {"certifi", "pyinstaller"},
            self.dependency_names(extras["build"]),
        )
        self.assertEqual(
            {"numpy", "scipy"},
            self.dependency_names(extras["examples"]),
        )
        self.assertEqual(
            {"numpy", "pytest"},
            self.dependency_names(extras["test"]),
        )
        self.assertEqual(
            {"build", "twine"},
            self.dependency_names(extras["release"]),
        )

    def test_core_helpers_remain_plugin_neutral(self):
        helper_root = Path(__file__).resolve().parents[1] / "src/sigvue/helpers"
        modules = {
            path.relative_to(helper_root).as_posix()
            for path in helper_root.rglob("*.py")
        }
        self.assertEqual(
            {
                "__init__.py",
                "config.py",
                "downloads.py",
                "formatting.py",
            },
            modules,
        )
        for module in modules:
            contents = (helper_root / module).read_text(encoding="utf-8")
            self.assertNotIn("sigvue.plugin", contents)

    def test_public_typing_marker_is_installed_as_package_data(self):
        self.assertTrue(files("sigvue").joinpath("py.typed").is_file())

    def test_standalone_build_support_is_installed_as_package_data(self):
        resources = files("sigvue._packaging")
        self.assertTrue(resources.joinpath("sigvue.spec").is_file())


if __name__ == "__main__":
    unittest.main()
