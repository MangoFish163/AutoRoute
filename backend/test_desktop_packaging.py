import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from desktop_entry import checked_tree, cleanup


class DesktopMaintenanceTests(unittest.TestCase):
    def test_default_cleanup_preserves_configuration_and_external_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            data = home / ".local-model-router"
            log = home / "local" / "LocalModelRouter"
            external = home / "external"
            for path in [data, log, external]:
                path.mkdir(parents=True)
                (path / "marker").write_text("keep")
            with (
                patch("desktop_entry.Path.home", return_value=home),
                patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": str(home / "local"), "LOCAL_MODEL_ROUTER_HOME": str(external)},
                ),
            ):
                cleanup(False)
                self.assertTrue(data.exists())
                self.assertFalse(log.exists())
                cleanup(True)
                self.assertFalse(data.exists())
                self.assertTrue(external.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction safety")
    def test_junction_prevents_all_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = home / "local" / "LocalModelRouter"
            cache.mkdir(parents=True)
            external = home / "external"
            external.mkdir()
            marker = external / "do-not-delete"
            marker.write_text("keep")
            link = cache / "linked"
            # Paths are supplied as distinct native cmd arguments; no deletion through cmd.
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(external)], capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                with patch.dict(os.environ, {"LOCALAPPDATA": str(home / "local")}):
                    with self.assertRaises(ValueError):
                        cleanup(False)
                self.assertTrue(marker.exists())
                with self.assertRaises(ValueError):
                    checked_tree(link / "subdirectory")
            finally:
                link.rmdir()  # Remove only the junction itself.


if __name__ == "__main__":
    unittest.main()
