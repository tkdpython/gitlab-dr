import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from gitlab_dr.core import _checkout_project_files


class RepoFilesCheckoutTests(unittest.TestCase):
    def _mock_run_git(self, args, _env, cwd=None):
        if args[0] == "clone":
            clone_dir = args[2]
            os.makedirs(os.path.join(clone_dir, ".git"), exist_ok=True)
            os.makedirs(os.path.join(clone_dir, "tenants"), exist_ok=True)
            with open(os.path.join(clone_dir, "tenants", "README.md"), "w", encoding="utf-8") as fh:
                fh.write("hello\n")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if args[0] == "ls-files":
            return SimpleNamespace(returncode=0, stdout=b"tenants/README.md\n", stderr=b"")
        raise AssertionError("unexpected git command: %r" % (args,))

    @mock.patch("gitlab_dr.core._run_git")
    def test_checkout_overwrites_existing_destination_tree(self, run_git):
        run_git.side_effect = self._mock_run_git

        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = os.path.join(tmpdir, "repos", "heimdall", "iac-heimdall-tenants")
            existing_subdir = os.path.join(dest_dir, "tenants")
            os.makedirs(existing_subdir, exist_ok=True)
            with open(os.path.join(existing_subdir, "stale.txt"), "w", encoding="utf-8") as fh:
                fh.write("stale\n")

            wrote_files = _checkout_project_files(
                project_path="heimdall/iac-heimdall-tenants",
                clone_url="https://example.invalid/heimdall/iac-heimdall-tenants.git",
                git_env={},
                dest_dir=dest_dir,
            )

            self.assertTrue(wrote_files)
            self.assertTrue(os.path.isfile(os.path.join(dest_dir, "tenants", "README.md")))
            self.assertFalse(os.path.exists(os.path.join(dest_dir, "tenants", "stale.txt")))


if __name__ == "__main__":
    unittest.main()
