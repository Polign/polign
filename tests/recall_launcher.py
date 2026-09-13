"""Check launcher selection without touching Claude settings or a real server."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

START = Path(__file__).resolve().parents[1] / "plugins/recall/scripts/start.sh"


class LauncherTests(unittest.TestCase):
    def test_saved_launcher_wins_over_path_and_environment(self):
        with tempfile.TemporaryDirectory(prefix="recall 'launcher ") as tmp:
            root = Path(tmp)
            launch = root / "launch"
            launch.write_text("#!/bin/sh\nprintf '%s\\n' saved-connection\n")
            launch.chmod(0o700)
            env = os.environ | {"POLIGN_RECALL_HOME": tmp, "POLIGN_BIN": "/does/not/exist", "PATH": "/does/not/exist"}
            result = subprocess.run(["/bin/sh", str(START)], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, "saved-connection\n")
            self.assertEqual(result.stderr, "")

    def test_manual_connection_keeps_environment_and_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "polign"
            cli.write_text("#!/bin/sh\nif [ \"$2\" = '-help' ]; then echo '-memory-only' >&2; exit 0; fi\nprintf '%s\\n' \"$*\" \"$POLIGN_URL\" \"$POLIGN_COLLECTION\"\n")
            cli.chmod(0o700)
            env = os.environ | {"POLIGN_RECALL_HOME": tmp, "POLIGN_BIN": str(cli), "POLIGN_URL": "http://localhost:23100", "POLIGN_COLLECTION": "existing-memory"}
            result = subprocess.run(["/bin/sh", str(START)], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ["mcp -memory-only -write", "http://localhost:23100", "existing-memory"])


    def test_unrunnable_launcher_stops_instead_of_finding_another_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "launch").write_text("#!/bin/sh\nprintf '%s\\n' saved-connection\n")
            (root / "launch").chmod(0o600)  # present, not executable
            cli = root / "polign"
            cli.write_text("#!/bin/sh\nif [ \"$2\" = '-help' ]; then echo '-memory-only' >&2; exit 0; fi\nprintf other-database\n")
            cli.chmod(0o700)
            env = os.environ | {"POLIGN_RECALL_HOME": tmp, "POLIGN_BIN": str(cli)}
            result = subprocess.run(["/bin/sh", str(START)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("other-database", result.stdout)
            self.assertIn("cannot execute it", result.stderr)


if __name__ == "__main__":
    unittest.main()
