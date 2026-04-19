import os
import tempfile
import unittest
from unittest import mock

from gitlab_dr.cli import build_parser, resolve_password
from gitlab_dr.core import (
    GitLabDRError,
    archive_is_encrypted,
    build_backup,
    read_backup_archive,
    restore_backup,
    write_backup_archive,
)


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


class BackupRestoreFlowTests(unittest.TestCase):
    def test_build_backup_collects_groups_and_projects(self):
        client = mock.Mock()
        root_group = {"id": 1, "parent_id": None, "name": "root", "path": "root", "full_path": "root"}
        sub_group = {"id": 2, "parent_id": 1, "name": "sub", "path": "sub", "full_path": "root/sub"}
        client.list_groups.return_value = [root_group, sub_group]
        client.group_variables.side_effect = lambda group_id: [{"key": "GV", "value": str(group_id)}]
        client.group_members.return_value = []
        client.group_projects.side_effect = lambda group_id: [{"id": 100, "name": "proj", "path": "proj"}] if group_id == 1 else []
        client.project_details.return_value = {"id": 100, "name": "proj", "path": "proj"}
        client.project_variables.return_value = [{"key": "PV", "value": "1"}]
        client.project_merge_requests.return_value = []

        payload = build_backup(client)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["groups"][0]["details"]["id"], 1)
        self.assertEqual(payload["groups"][0]["subgroups"][0]["details"]["id"], 2)
        self.assertEqual(payload["groups"][0]["projects"][0]["details"]["id"], 100)

    def test_restore_backup_replays_groups_projects_vars_and_mrs(self):
        client = mock.Mock()
        client.get_group.return_value = None
        client.create_group.side_effect = [
            {"id": 10, "full_path": "root"},
            {"id": 11, "full_path": "root/sub"},
        ]
        client.project_exists.return_value = None
        client.create_project.return_value = {"id": 20}
        client.project_merge_requests.return_value = []

        payload = {
            "groups": [
                {
                    "details": {"name": "root", "path": "root", "full_path": "root"},
                    "variables": [{"key": "A", "value": "1"}],
                    "projects": [
                        {
                            "details": {"name": "p", "path": "p"},
                            "variables": [{"key": "B", "value": "2"}],
                            "merge_requests": [
                                {"title": "MR", "source_branch": "feature", "target_branch": "main"}
                            ],
                        }
                    ],
                    "subgroups": [
                        {
                            "details": {"name": "sub", "path": "sub", "full_path": "root/sub"},
                            "variables": [],
                            "projects": [],
                            "subgroups": [],
                        }
                    ],
                }
            ]
        }

        restore_backup(client, payload)

        client.create_group.assert_any_call(name="root", path="root", parent_id=None, visibility=None)
        client.create_group.assert_any_call(name="sub", path="sub", parent_id=10, visibility=None)
        client.create_project.assert_called_once_with(namespace_id=10, name="p", path="p", visibility=None)
        client.upsert_group_variable.assert_called_once()
        client.upsert_project_variable.assert_called_once()
        client.create_merge_request.assert_called_once_with(20, "MR", "feature", "main")


if __name__ == "__main__":
    unittest.main()
