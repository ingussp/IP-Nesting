"""Platform selection does not require FreeCAD or any foreign binaries."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from IPNestingRuntime import executable_candidates, find_executable


class RuntimeTests(unittest.TestCase):
    def test_supported_platform_paths(self):
        for system, machine, suffix in [
                ('Windows', 'AMD64', 'clinesting/windows-x86_64/clinesting.exe'),
                ('Linux', 'x86_64', 'clinesting/linux-x86_64/clinesting'),
                ('Linux', 'aarch64', 'clinesting/linux-aarch64/clinesting'),
                ('Darwin', 'arm64', 'clinesting/macos-universal2/clinesting'),
                ('Darwin', 'x86_64', 'clinesting/macos-universal2/clinesting')]:
            self.assertTrue(Path(executable_candidates('.', system, machine)[0]).as_posix().endswith(suffix))

    def test_missing_and_unsupported_binary(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(find_executable(root, 'Windows', 'AMD64'))
            with self.assertRaises(RuntimeError):
                find_executable(root, 'Windows', 'ARM64')

    def test_macos_universal_and_specific_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            universal, specific = executable_candidates(root, 'Darwin', 'arm64')
            Path(specific).parent.mkdir(parents=True)
            Path(specific).write_text('specific placeholder')
            import os
            from unittest.mock import patch
            with patch.object(os, 'access', return_value=True):
                self.assertEqual(find_executable(root, 'Darwin', 'arm64'), specific)
                Path(universal).parent.mkdir()
                Path(universal).write_text('universal placeholder')
                self.assertEqual(find_executable(root, 'Darwin', 'arm64'), universal)


if __name__ == '__main__':
    unittest.main()
