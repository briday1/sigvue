import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sigvue.profile import load_browser_profile
from sigvue.web.application import create_app


class BrowserProfileTests(unittest.TestCase):
    def test_repository_entry_point_can_create_multiple_configured_instances(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "radar-repository"
            package_name = f"radar_workspace_{uuid4().hex}"
            package = repository / "src" / package_name
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "from tests.fixtures import create_workspace as fixture_workspace\n"
                "def create_workspace(config):\n"
                "    return fixture_workspace()\n",
                encoding="utf-8",
            )
            (repository / "pyproject.toml").write_text(
                "[project]\n"
                f"name = '{package_name}'\n"
                "version = '0.1.0'\n"
                "[project.entry-points.\"sigvue.workspaces\"]\n"
                f"radar-analysis = '{package_name}:create_workspace'\n",
                encoding="utf-8",
            )
            profile_path = root / "browser.toml"
            profile_path.write_text(
                "[browser]\n"
                "title = 'Lab Browser'\n"
                "subtitle = 'Review laboratory recordings'\n"
                "[[workspaces]]\n"
                "use = 'radar-analysis'\n"
                "path = './radar-repository'\n"
                "id = 'lab-captures'\n"
                "name = 'Lab captures'\n"
                "description = 'Laboratory waterfall review'\n"
                "category = 'laboratory'\n"
                "tags = ['configured', 'lab']\n"
                "[workspaces.config]\n"
                "data_root = './data/lab'\n"
                "[[workspaces]]\n"
                "use = 'radar-analysis'\n"
                "path = './radar-repository'\n"
                "id = 'field-tests'\n"
                "name = 'Field tests'\n"
                "[workspaces.config]\n"
                "data_root = './data/field'\n",
                encoding="utf-8",
            )

            try:
                profile = load_browser_profile(profile_path)
                self.assertEqual("Lab Browser", profile.title)
                self.assertEqual("Review laboratory recordings", profile.subtitle)
                self.assertEqual(repository.resolve(), profile.workspaces[0].watch_path)
                self.assertEqual(str((root / "data/lab").resolve()), profile.workspaces[0].configuration["data_root"])

                app = create_app(config_path=profile_path)
                self.assertEqual("Lab Browser", app.title)
                self.assertEqual("Review laboratory recordings", app.subtitle)
                self.assertEqual(
                    [("lab-captures", "Lab captures"), ("field-tests", "Field tests")],
                    [(workspace["id"], workspace["name"]) for workspace in app.list_workspaces()],
                )
                self.assertEqual("Laboratory waterfall review", app.list_workspaces()[0]["description"])
                self.assertEqual("laboratory", app.list_workspaces()[0]["category"])
                self.assertEqual(["configured", "lab"], app.list_workspaces()[0]["tags"])

                profile_path.write_text(
                    "[browser]\n"
                    "title = 'Reloaded Browser'\n"
                    "subtitle = 'Updated without restart'\n"
                    "[[workspaces]]\n"
                    "use = 'radar-analysis'\n"
                    "path = './radar-repository'\n"
                    "id = 'reloaded-captures'\n"
                    "name = 'Reloaded captures'\n",
                    encoding="utf-8",
                )
                app_identity = id(app)
                self.assertTrue(app.reload_browser_profile())
                self.assertEqual(app_identity, id(app))
                self.assertEqual("Reloaded Browser", app.title)
                self.assertEqual("Updated without restart", app.subtitle)
                self.assertEqual(
                    [("reloaded-captures", "Reloaded captures")],
                    [(workspace["id"], workspace["name"]) for workspace in app.list_workspaces()],
                )
                profile_path.write_text("[[workspaces]]\nuse = 'does-not-exist'\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unknown workspace 'does-not-exist'"):
                    app.reload_browser_profile()
                self.assertEqual(
                    [("reloaded-captures", "Reloaded captures")],
                    [(workspace["id"], workspace["name"]) for workspace in app.list_workspaces()],
                )
            finally:
                sys.modules.pop(package_name, None)

    def test_direct_module_factory_reference_does_not_require_entry_point(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            module_name = f"direct_workspace_{uuid4().hex}"
            (root / f"{module_name}.py").write_text(
                "from tests.fixtures import create_workspace as fixture_workspace\n"
                "def build(config):\n"
                "    return fixture_workspace(config)\n",
                encoding="utf-8",
            )
            profile_path = root / "browser.toml"
            profile_path.write_text(
                "[[workspaces]]\n"
                f"use = '{module_name}:build'\n"
                "path = '.'\n"
                "id = 'direct'\n"
                "name = 'Direct module'\n",
                encoding="utf-8",
            )
            try:
                app = create_app(config_path=profile_path)
                self.assertEqual(["direct"], [workspace["id"] for workspace in app.list_workspaces()])
            finally:
                sys.modules.pop(module_name, None)

    def test_unknown_workspace_name_reports_available_entry_points(self):
        with TemporaryDirectory() as directory:
            profile_path = Path(directory) / "browser.toml"
            profile_path.write_text("[[workspaces]]\nuse = 'does-not-exist'\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown workspace 'does-not-exist'"):
                load_browser_profile(profile_path)


if __name__ == "__main__":
    unittest.main()
