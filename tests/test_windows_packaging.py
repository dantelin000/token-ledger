"""Cross-platform checks for installer extraction and uninstall boundaries."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from scripts.build_windows_installer import extract_runtime, extract_tzdata, payload_manifests
from scripts.build_desktop import source_archive


class WindowsPackagingTests(unittest.TestCase):
    def test_runtime_rejects_archive_escape_and_links(self):
        for name, kind in (("python/../../outside.txt", tarfile.REGTYPE),
                           ("/python/absolute.txt", tarfile.REGTYPE),
                           ("python/Lib/link", tarfile.SYMTYPE),
                           ("python/Lib/hardlink", tarfile.LNKTYPE)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                archive = base / "runtime.tar.gz"
                with tarfile.open(archive, "w:gz") as target:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = "../../outside.txt"
                    target.addfile(member)
                with self.assertRaises(ValueError):
                    extract_runtime(archive, base / "payload")
                self.assertFalse((base / "outside.txt").exists())

    def test_timezone_wheel_cannot_add_an_unexpected_package(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            wheel = base / "tzdata.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("tokenlog/desktop.py", "unexpected overwrite")
            with self.assertRaises(ValueError):
                extract_tzdata(wheel, base / "app")
            self.assertFalse((base / "app/tokenlog/desktop.py").exists())

    def test_uninstall_manifest_spares_unlisted_files_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            payload, program = base / "payload", base / "installed"
            (payload / "app/tokenlog").mkdir(parents=True)
            (payload / "app/tokenlog/desktop.py").write_text("# app", encoding="utf-8")
            (payload / "Token Ledger.exe").write_bytes(b"test")
            program.mkdir()
            import shutil
            shutil.copytree(payload, program, dirs_exist_ok=True)
            (program / "my-notes.txt").write_text("preserve", encoding="utf-8")
            (base / "TokenLedger").mkdir()
            ledger = base / "TokenLedger/token-usage.sqlite3"
            ledger.write_bytes(b"private ledger sentinel")
            _, uninstall = payload_manifests(payload, base)
            # Exercise the generated exact-path delete plan in a temporary tree.
            for line in uninstall.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("  Delete ") or line.startswith("  RMDir "):
                    relative = line.split('"$INSTDIR\\', 1)[1].removesuffix('"')
                    target = program.joinpath(*relative.split("\\"))
                    self.assertTrue(target.is_relative_to(program))
                    if line.startswith("  Delete "):
                        target.unlink()
                    else:
                        target.rmdir()
            self.assertEqual((program / "my-notes.txt").read_text(), "preserve")
            self.assertEqual(ledger.read_bytes(), b"private ledger sentinel")
            self.assertFalse((program / "app").exists())

    def test_clean_source_contains_rebuildable_installer_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = source_archive(Path(directory))
            with zipfile.ZipFile(archive) as source:
                names = set(source.namelist())
                for relative in ("packaging/windows/installer.nsi", "packaging/windows/launcher.nsi",
                                 "packaging/windows/dependencies.json", "packaging/windows/start.py",
                                 "scripts/build_windows_installer.py", "scripts/verify_windows_installer.py"):
                    self.assertIn("TokenLedger/" + relative, names)
                self.assertFalse(any("/build/" in name or "/dist/" in name for name in names))
                launcher = source.read("TokenLedger/启动用量统计.bat")
                self.assertIn(b"\r\n", launcher)
                self.assertNotIn(b"\\r\\n", launcher)


if __name__ == "__main__":
    unittest.main()
