"""Rename remains a rename, including Windows syntax on every test platform."""
import tempfile
import errno
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from actions import file_controller as files


class RenameSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "allowed"
        self.root.mkdir()
        self.source = self.root / "original.txt"
        self.source.write_text("preserve", encoding="utf-8")
        self.scope = patch.object(files, "_SAFE_ROOTS", [self.root])
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.undo = patch.object(files, "push_undo")
        self.undo_mock = self.undo.start()
        self.addCleanup(self.undo.stop)

    def test_simple_name_renames_without_changing_directory(self):
        result = files.rename_file(str(self.source), new_name="renamed.txt")
        self.assertTrue(result.startswith("Renamed"), result)
        self.assertEqual((self.root / "renamed.txt").read_text(), "preserve")

    def test_absolute_destination_is_rejected(self):
        outside = Path(self.temp.name) / "outside.txt"
        result = files.rename_file(str(self.source), new_name=str(outside))
        self.assertTrue(self.source.exists(), result)
        self.assertFalse(outside.exists())

    def test_traversal_and_invalid_windows_names_are_rejected(self):
        for name in ("../outside.txt", "..\\outside.txt", "a/b", "a\\b", ".", "..",
                     "", "C:\\outside.txt", "C:outside.txt", "\\\\server\\x",
                     "CON", "aux.txt", "LPT1.log", "COM9", "nul ", "a.",
                     "a ", "a:b", "a?b", "a*b", "a\x00b", "a\nb",
                     "COM¹.txt", "LPT².log", "CONIN$", "CONOUT$", "a\x7fb"):
            with self.subTest(name=repr(name)):
                # Each case owns a fresh source, even against the faulty baseline.
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    source = Path(directory) / "source.txt"
                    source.write_text("preserve")
                    result = files.rename_file(str(source), new_name=name)
                    self.assertTrue(source.exists(), result)
                    self.assertFalse(result.startswith("Renamed"), result)

    def test_existing_destination_is_not_overwritten(self):
        destination = self.root / "existing.txt"
        destination.write_text("keep", encoding="utf-8")
        files.rename_file(str(self.source), new_name=destination.name)
        self.assertTrue(self.source.exists())
        self.assertEqual(destination.read_text(), "keep")

    def test_symlink_parent_outside_allowed_root_is_denied(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        other = outside / "data.txt"
        other.write_text("keep")
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        files.rename_file(str(link / "data.txt"), new_name="renamed.txt")
        self.assertTrue(other.exists())

    def test_destination_appearing_during_rename_is_not_overwritten(self):
        destination = self.root / "raced.txt"
        original_rename = Path.rename

        def race(source, target):
            destination.write_text("keep")
            return original_rename(source, target)

        with patch.object(Path, "rename", race):
            files.rename_file(str(self.source), new_name=destination.name)
        self.assertEqual(destination.read_text(), "keep")
        self.assertTrue(self.source.exists())

    def test_directory_rename_preserves_contents_and_supports_undo(self):
        original = self.root / "folder"
        original.mkdir()
        (original / "data.txt").write_text("keep")
        result = files.rename_file(str(original), new_name="renamed folder")
        self.assertTrue(result.startswith("Renamed"), result)
        self.assertEqual((self.root / "renamed folder" / "data.txt").read_text(), "keep")
        restore = self.undo_mock.call_args.args[1]
        self.assertIn("back", restore())
        self.assertEqual((original / "data.txt").read_text(), "keep")

    def test_undo_does_not_overwrite_a_new_entry_at_the_original_name(self):
        files.rename_file(str(self.source), new_name="renamed.txt")
        restore = self.undo_mock.call_args.args[1]
        self.source.write_text("new user data")
        self.assertIn("Cannot undo", restore())
        self.assertEqual(self.source.read_text(), "new user data")
        self.assertEqual((self.root / "renamed.txt").read_text(), "preserve")

    def test_mocked_junction_parent_resolving_outside_root_is_denied(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("keep")
        junction = self.root / "junction"
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            return outside if path == junction else original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve):
            result = files.rename_file(str(junction / "data.txt"), new_name="renamed.txt")
        self.assertIn("Access denied", result)
        self.assertTrue((outside / "data.txt").exists())

    def test_mocked_reparse_source_is_denied_without_following_it(self):
        original_lstat = Path.lstat

        def lstat(path):
            info = original_lstat(path)
            if path == self.source:
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                       st_ino=info.st_ino, st_file_attributes=0x400)
            return info

        with patch.object(Path, "lstat", lstat):
            result = files.rename_file(str(self.source), new_name="renamed.txt")
        self.assertIn("Access denied", result)
        self.assertTrue(self.source.exists())

    def test_mocked_parent_swap_before_operation_is_detected(self):
        original_lstat = Path.lstat

        def lstat(path):
            info = original_lstat(path)
            if path == self.root:
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return info

        with patch.object(Path, "lstat", lstat):
            result = files.rename_file(str(self.source), new_name="renamed.txt")
        self.assertIn("Access denied", result)
        self.assertTrue(self.source.exists())

    def test_mocked_source_swap_before_operation_is_detected(self):
        original_lstat = Path.lstat
        calls = 0

        def lstat(path):
            nonlocal calls
            info = original_lstat(path)
            if path == self.source:
                calls += 1
                if calls > 1:
                    return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return info

        with patch.object(Path, "lstat", lstat):
            result = files.rename_file(str(self.source), new_name="renamed.txt")
        self.assertIn("Access denied", result)
        self.assertTrue(self.source.exists())

    def test_invalid_source_types_return_controlled_errors(self):
        for value in (None, 7, [], {}):
            with self.subTest(value=value):
                self.assertIn("Invalid source", files.rename_file(value, new_name="valid.txt"))
        self.assertTrue(self.source.exists())


class NativeExclusiveRenameTests(unittest.TestCase):
    def test_posix_bindings_use_exclusive_flags_and_same_open_parent(self):
        for system, symbol, flag in (("Linux", "renameat2", 1), ("Darwin", "renameatx_np", 4)):
            with self.subTest(system=system):
                function = Mock(return_value=0)
                library = SimpleNamespace(**{symbol: function})
                with patch.object(files, "_OS", system), patch.object(files.ctypes, "CDLL", return_value=library):
                    files._native_rename_noreplace(Path("source.txt"), Path("new.txt"), 23)
                function.assert_called_once_with(23, b"source.txt", 23, b"new.txt", flag)

    def test_existing_destination_errno_never_falls_back_to_overwriting(self):
        function = Mock(return_value=-1)
        with patch.object(files, "_OS", "Linux"), patch.object(
            files.ctypes, "CDLL", return_value=SimpleNamespace(renameat2=function)
        ), patch.object(files.ctypes, "get_errno", return_value=errno.EEXIST):
            with self.assertRaises(FileExistsError):
                files._native_rename_noreplace(Path("source"), Path("destination"), 23)

    def test_unsupported_filesystem_and_missing_api_fail_safely(self):
        with patch.object(files, "_OS", "Linux"), patch.object(
            files.ctypes, "CDLL", return_value=SimpleNamespace(renameat2=Mock(return_value=-1))
        ), patch.object(files.ctypes, "get_errno", return_value=errno.EINVAL):
            with self.assertRaises(NotImplementedError):
                files._native_rename_noreplace(Path("source"), Path("destination"), 23)
        with patch.object(files.ctypes, "CDLL", return_value=SimpleNamespace()):
            with self.assertRaises(NotImplementedError):
                files._native_rename_noreplace(Path("source"), Path("destination"), 23)
