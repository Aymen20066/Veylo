"""Importing the veylo package must never start (or even import) Streamlit."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class NoStreamlitImportTests(unittest.TestCase):
    def test_importing_every_veylo_module_does_not_import_streamlit(self):
        code = (
            "import sys\n"
            "from veylo import config, costs, retrieval, screening, transform\n"
            "sys.exit(1 if 'streamlit' in sys.modules else 0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
