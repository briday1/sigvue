import unittest
from importlib.resources import files


class PackagingTests(unittest.TestCase):
    def test_public_typing_marker_is_installed_as_package_data(self):
        self.assertTrue(files("sigvue").joinpath("py.typed").is_file())

    def test_standalone_build_support_is_installed_as_package_data(self):
        resources = files("sigvue._packaging")
        self.assertTrue(resources.joinpath("sigvue.spec").is_file())


if __name__ == "__main__":
    unittest.main()
