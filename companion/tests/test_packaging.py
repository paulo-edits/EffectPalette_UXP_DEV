"""Guards the packaging wiring that gives the product a real identity in Windows."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "packaging" / "pyinstaller" / "FXPalette.spec"
ISS = REPO / "packaging" / "inno" / "FXPalette.iss"
ICO = REPO / "companion" / "assets" / "fx_palette.ico"
PNG = REPO / "companion" / "assets" / "fx_palette.png"


class AppIdentityTests(unittest.TestCase):
    def test_icon_assets_exist(self):
        self.assertTrue(ICO.exists(), "companion/assets/fx_palette.ico is missing")
        self.assertTrue(PNG.exists(), "companion/assets/fx_palette.png is missing")

    def test_pyinstaller_spec_sets_the_exe_icon(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("fx_palette.ico", text)
        self.assertIn("icon=", text)

    def test_pyinstaller_spec_declares_a_version_resource(self):
        text = SPEC.read_text(encoding="utf-8")
        self.assertIn("paulo.edits", text)
        self.assertIn("FX.palette", text)

    def test_inno_publisher_is_the_brand_spelling(self):
        text = ISS.read_text(encoding="utf-8")
        self.assertIn('#define MyAppPublisher "paulo.edits"', text)
        self.assertNotIn("Paulo Edits", text)

    def test_inno_uses_the_icon_for_setup_and_uninstall(self):
        text = ISS.read_text(encoding="utf-8")
        self.assertIn("SetupIconFile=", text)
        # The uninstall entry must point at a real .ico, not the icon-less exe.
        self.assertIn(r"UninstallDisplayIcon={app}\fx_palette.ico", text)


if __name__ == "__main__":
    unittest.main()
