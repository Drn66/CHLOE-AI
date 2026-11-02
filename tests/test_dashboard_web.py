"""Browser JavaScript regressions, discovered by the existing unittest runner."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DashboardBrowserTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript regressions")
    def test_real_login_javascript(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", str(root / "tests" / "login_web.test.cjs")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser JavaScript regressions")
    def test_real_dashboard_javascript(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", str(root / "tests" / "dashboard_web.test.cjs")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
