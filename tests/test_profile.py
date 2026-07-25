import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sigvue.profile import (
    WorkspaceLaunchSpec,
    append_workspace_to_profile,
    load_browser_profile,
    workspace_factory_catalog,
    workspace_launch_spec,
)
from sigvue.web.application import create_app


class BrowserProfileTests(unittest.TestCase):
    def test_catalog_prefers_the_selected_projects_local_examples(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            examples = root / "examples"
            examples.mkdir()
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'local-project'\nversion = '0.1.0'\n",
                encoding="utf-8",
            )
            (examples / "browser.toml").write_text(
                "[[workspaces]]\n"
                "use = 'tests.fixtures:create_workspace'\n"
                "path = '..'\n"
                "id = 'local-example'\n"
                "name = 'Local example'\n"
                "description = 'Declared by this source tree'\n"
                "tags = ['local']\n"
                "[workspaces.config]\n"
                "data_root = './data'\n",
                encoding="utf-8",
            )

            factories = workspace_factory_catalog(root)

            self.assertEqual(1, len(factories))
            self.assertEqual("Local example", factories[0]["name"])
            self.assertEqual(
                "tests.fixtures:create_workspace",
                factories[0]["use"],
            )
            self.assertEqual("local-project", factories[0]["package"])
            self.assertEqual(str(root.resolve()), factories[0]["repository"])
            self.assertEqual(
                str((examples / "data").resolve()),
                factories[0]["defaults"]["config"]["data_root"],
            )

    def test_session_workspace_can_be_created_without_a_profile(self):
        spec = WorkspaceLaunchSpec(
            "tests.fixtures",
            "create_workspace",
            {"id": "session-data", "name": "Session data"},
            metadata_overrides={
                "identifier": "session-data",
                "display_name": "Session data",
                "description": "Opened without TOML",
                "category": "session",
                "tags": ("temporary",),
            },
            reference="tests.fixtures:create_workspace",
        )

        app = create_app(workspace_specs=(spec,))

        self.assertIsNone(app.config_path)
        self.assertEqual(
            [("session-data", "Session data")],
            [
                (workspace["id"], workspace["name"])
                for workspace in app.list_workspaces()
            ],
        )
        self.assertEqual(
            "Opened without TOML",
            app.list_workspaces()[0]["description"],
        )

    def test_session_workspace_survives_profile_reload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "browser.toml"
            profile_path.write_text(
                "[[workspaces]]\n"
                "use = 'tests.fixtures:create_workspace'\n"
                "id = 'profile-data'\n"
                "name = 'Profile data'\n",
                encoding="utf-8",
            )
            app = create_app(config_path=profile_path)
            app.configure_workspace(
                {
                    "use": "tests.fixtures:create_workspace",
                    "id": "session-data",
                    "name": "Session data",
                }
            )

            self.assertTrue(app.reload_browser_profile())

            self.assertEqual(
                {"profile-data", "session-data"},
                {workspace["id"] for workspace in app.list_workspaces()},
            )

    def test_session_workspace_can_be_promoted_to_a_profile(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "captures"
            data_root.mkdir()
            profile_path = root / "saved-browser.toml"
            app = create_app()

            result = app.configure_workspace(
                {
                    "use": "tests.fixtures:create_workspace",
                    "id": "saved-data",
                    "name": "Saved data",
                    "description": "Reusable session",
                    "category": "test",
                    "tags": ["saved", "local"],
                    "flatten_discovery": True,
                    "config": {
                        "data_root": str(data_root),
                        "gain": 2.5,
                        "nested": {"enabled": True},
                    },
                },
                persist_path=profile_path,
            )

            self.assertEqual(str(profile_path.resolve()), result["persisted_to"])
            self.assertEqual(profile_path.resolve(), app.config_path)
            profile = load_browser_profile(profile_path)
            saved = profile.workspaces[0]
            self.assertEqual("tests.fixtures:create_workspace", saved.reference)
            self.assertEqual(str(data_root), saved.configuration["data_root"])
            self.assertEqual(2.5, saved.configuration["gain"])
            self.assertEqual({"enabled": True}, saved.configuration["nested"])
            self.assertTrue(saved.flatten_discovery)
            self.assertEqual("Saved data", app.list_workspaces()[0]["name"])

            with self.assertRaisesRegex(
                ValueError,
                "already exists",
            ):
                append_workspace_to_profile(profile_path, saved)

    def test_failed_profile_save_rolls_back_the_session_workspace(self):
        with TemporaryDirectory() as directory:
            profile_path = Path(directory) / "browser.toml"
            profile_path.write_text(
                "[[workspaces]]\n"
                "use = 'tests.fixtures:create_workspace'\n"
                "id = 'duplicate'\n"
                "name = 'Existing workspace'\n",
                encoding="utf-8",
            )
            app = create_app()

            with self.assertRaisesRegex(ValueError, "already exists"):
                app.configure_workspace(
                    {
                        "use": "tests.fixtures:create_workspace",
                        "id": "duplicate",
                        "name": "Unsaved duplicate",
                    },
                    persist_path=profile_path,
                )

            self.assertEqual([], app.list_workspaces())
            self.assertIsNone(app.config_path)

    def test_session_can_override_flattened_discovery(self):
        app = create_app()

        app.configure_workspace(
            {
                "use": "tests.fixtures:create_workspace",
                "id": "flat-data",
                "name": "Flat data",
                "flatten_discovery": True,
            }
        )

        self.assertTrue(app.registry.get("flat-data").flatten_discovery)

    def test_flatten_discovery_requires_a_boolean(self):
        with self.assertRaisesRegex(
            ValueError,
            "flatten_discovery must be true or false",
        ):
            workspace_launch_spec(
                {
                    "use": "tests.fixtures:create_workspace",
                    "flatten_discovery": "yes",
                },
                Path.cwd(),
            )

    def test_profile_shaped_entry_resolves_relative_paths_for_session_use(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = workspace_launch_spec(
                {
                    "use": "tests.fixtures:create_workspace",
                    "id": "relative-data",
                    "name": "Relative data",
                    "config": {"data_root": "./captures"},
                },
                root,
            )

            self.assertEqual(
                str((root / "captures").resolve()),
                spec.configuration["data_root"],
            )
            self.assertEqual(
                "tests.fixtures:create_workspace",
                spec.reference,
            )

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
