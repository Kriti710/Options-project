"""Foundation-level package checks."""

import unittest

import nifty_vol


class PackageTest(unittest.TestCase):
    def test_package_exposes_version(self) -> None:
        self.assertEqual(nifty_vol.__version__, "0.1.0")


if __name__ == "__main__":
    unittest.main()
