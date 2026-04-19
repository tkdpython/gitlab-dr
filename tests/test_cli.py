import os
import tempfile
import unittest
from unittest import mock

from gitlab_dr.cli import build_parser, resolve_password
from gitlab_dr.core import GitLabDRError, archive_is_encrypted, read_backup_archive, write_backup_archive


class ParserTests(unittest.TestCase):
    def test_requires_mode(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--source", "x", "--destination", "y"])

    def test_backup_parse(self):
        parser = build_parser()
        args = parser.parse_args(["--backup", "--source", "https://gitlab.example", "--destination", "out.zip"])
        self.assertTrue(args.backup)
        self.assertEqual(args.destination, "out.zip")


class PasswordTests(unittest.TestCase):
    @mock.patch.dict(os.environ, {"GITLAB_DR_PASSWORD": "abc"}, clear=True)
    def test_uses_env_password(self):
        self.assertEqual(resolve_password(True, "backup"), "abc")

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("getpass.getpass", side_effect=["pw1", "pw2"])
    def test_backup_password_mismatch(self, _getpass):
        with self.assertRaises(GitLabDRError):
            resolve_password(True, "backup")

    @mock.patch.dict(os.environ, {}, clear=True)
    @mock.patch("getpass.getpass", return_value="pw")
    def test_restore_prompt(self, _getpass):
        self.assertEqual(resolve_password(True, "restore"), "pw")


class ArchiveTests(unittest.TestCase):
    def test_write_read_unencrypted_archive(self):
        payload = {"groups": [{"name": "example"}]}
        with tempfile.NamedTemporaryFile(suffix=".zip") as handle:
            write_backup_archive(handle.name, payload, encrypt=False)
            self.assertFalse(archive_is_encrypted(handle.name))
            restored = read_backup_archive(handle.name)
        self.assertEqual(restored, payload)

    def test_write_read_encrypted_archive(self):
        payload = {"groups": [{"name": "example"}]}
        with tempfile.NamedTemporaryFile(suffix=".zip") as handle:
            write_backup_archive(handle.name, payload, encrypt=True, password="secret")
            self.assertTrue(archive_is_encrypted(handle.name))
            restored = read_backup_archive(handle.name, password="secret")
        self.assertEqual(restored, payload)


if __name__ == "__main__":
    unittest.main()
