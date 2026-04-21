import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import quote, urlparse, urlunparse

import pyzipper
import requests


class GitLabDRError(Exception):
    pass


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


class RunReport:
    def __init__(self):
        self.warnings = []
        self._lines = []

    def info(self, msg):
        _log(msg)
        self._lines.append(msg)

    def warn(self, msg):
        full = "warning: " + msg
        _log(full)
        self._lines.append(full)
        self.warnings.append(msg)

    def print_summary(self):
        _log("")
        self._lines.append("")
        if not self.warnings:
            _log("summary: completed with no warnings")
            self._lines.append("summary: completed with no warnings")
            return
        summary = "summary: %d warning(s):" % len(self.warnings)
        _log(summary)
        self._lines.append(summary)
        for w in self.warnings:
            line = "  - " + w
            _log(line)
            self._lines.append(line)

    def write_log(self, path):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self._lines))
                fh.write("\n")
        except OSError as exc:
            _log("warning: could not write log file %s: %s" % (path, exc))


class GitLabClient(object):
    def __init__(self, base_url, token, cert=None, verify=True, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.cert = cert
        self.verify = verify
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"PRIVATE-TOKEN": token})
        self.session.verify = verify
        if cert:
            self.session.cert = cert

    def _url(self, path):
        return "%s/api/v4%s" % (self.base_url, path)

    def _request(self, method, path, params=None, payload=None, expected=None):
        response = self.session.request(
            method,
            self._url(path),
            params=params,
            json=payload,
            timeout=self.timeout,
        )
        if expected is None:
            expected = (200,)
        if response.status_code not in expected:
            raise GitLabDRError("GitLab API %s %s failed: %s %s" % (method, path, response.status_code, response.text))
        if response.status_code == 204:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def list_paginated(self, path, params=None, sudo=None):
        page = 1
        all_items = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        headers = {"Sudo": str(sudo)} if sudo is not None else {}
        while True:
            params["page"] = page
            response = self.session.get(self._url(path), params=params, headers=headers, timeout=self.timeout)
            if response.status_code != 200:
                raise GitLabDRError("GitLab API GET %s failed: %s %s" % (path, response.status_code, response.text))
            if not response.text:
                raise GitLabDRError(
                    "GitLab API GET %s returned status %s with empty body "
                    "(check base URL, token, and certificate)" % (path, response.status_code)
                )
            try:
                items = response.json()
            except ValueError as exc:
                raise GitLabDRError(
                    "GitLab API GET %s returned non-JSON response (status %s): %s"
                    % (path, response.status_code, response.text[:500])
                ) from exc
            all_items.extend(items)
            next_page = response.headers.get("X-Next-Page")
            if next_page:
                page = int(next_page)
                continue
            if len(items) < params["per_page"]:
                break
            page += 1
        return all_items

    def get_group(self, group_full_path):
        encoded = quote(group_full_path, safe="")
        try:
            return self._request("GET", "/groups/%s" % encoded)
        except GitLabDRError:
            return None

    def create_group(self, name, path, parent_id=None, visibility=None):
        payload = {"name": name, "path": path}
        if parent_id:
            payload["parent_id"] = parent_id
        if visibility:
            payload["visibility"] = visibility
        return self._request("POST", "/groups", payload=payload, expected=(201,))

    def list_groups(self):
        return self.list_paginated("/groups", params={"all_available": True})

    def group_variables(self, group_id):
        return self.list_paginated("/groups/%s/variables" % group_id)

    def group_members(self, group_id):
        return self.list_paginated("/groups/%s/members/all" % group_id)

    def list_subgroups(self, group_id):
        return self.list_paginated("/groups/%s/subgroups" % group_id)

    def group_projects(self, group_id):
        return self.list_paginated(
            "/groups/%s/projects" % group_id,
            params={"include_subgroups": False, "simple": False},
        )

    def project_details(self, project_id):
        return self._request("GET", "/projects/%s" % project_id)

    def project_variables(self, project_id, sudo=None):
        return self.list_paginated("/projects/%s/variables" % project_id, sudo=sudo)

    def project_members(self, project_id):
        return self.list_paginated("/projects/%s/members/all" % project_id)

    def project_merge_requests(self, project_id, state="all"):
        return self.list_paginated(
            "/projects/%s/merge_requests" % project_id,
            params={"state": state},
        )

    def project_exists(self, namespace_path, path):
        full = "%s/%s" % (namespace_path, path)
        encoded = quote(full, safe="")
        try:
            return self._request("GET", "/projects/%s" % encoded)
        except GitLabDRError:
            return None

    def create_project(self, namespace_id, name, path, visibility=None):
        payload = {"namespace_id": namespace_id, "name": name, "path": path}
        if visibility:
            payload["visibility"] = visibility
        return self._request("POST", "/projects", payload=payload, expected=(201,))

    def create_merge_request(self, project_id, title, source_branch, target_branch):
        payload = {
            "title": title,
            "source_branch": source_branch,
            "target_branch": target_branch,
        }
        return self._request(
            "POST",
            "/projects/%s/merge_requests" % project_id,
            payload=payload,
            expected=(201,),
        )

    def list_protected_branches(self, project_id):
        return self._request("GET", "/projects/%s/protected_branches" % project_id)

    def unprotect_branch(self, project_id, branch_name):
        encoded = quote(branch_name, safe="")
        self._request("DELETE", "/projects/%s/protected_branches/%s" % (project_id, encoded), expected=(204,))

    def protect_branch(self, project_id, branch_name, push_access_level=40, merge_access_level=40):
        payload = {
            "name": branch_name,
            "push_access_level": push_access_level,
            "merge_access_level": merge_access_level,
        }
        try:
            self._request("POST", "/projects/%s/protected_branches" % project_id, payload=payload, expected=(201,))
        except GitLabDRError:
            pass  # already protected — ignore

    def upsert_group_variable(self, group_id, variable):
        key = variable["key"]
        payload = {
            "key": key,
            "value": variable["value"],
            "masked": variable.get("masked", False),
            "protected": variable.get("protected", False),
            "environment_scope": variable.get("environment_scope", "*"),
        }
        encoded = quote(key, safe="")
        try:
            self._request("PUT", "/groups/%s/variables/%s" % (group_id, encoded), payload=payload)
        except GitLabDRError:
            self._request(
                "POST",
                "/groups/%s/variables" % group_id,
                payload=payload,
                expected=(201,),
            )

    def upsert_project_variable(self, project_id, variable):
        key = variable["key"]
        payload = {
            "key": key,
            "value": variable["value"],
            "masked": variable.get("masked", False),
            "protected": variable.get("protected", False),
            "environment_scope": variable.get("environment_scope", "*"),
        }
        encoded = quote(key, safe="")
        try:
            self._request("PUT", "/projects/%s/variables/%s" % (project_id, encoded), payload=payload)
        except GitLabDRError:
            self._request(
                "POST",
                "/projects/%s/variables" % project_id,
                payload=payload,
                expected=(201,),
            )


def _git_env(cert=None, verify=True, token=None):
    """Return a subprocess environment dict with git SSL settings applied."""
    env = os.environ.copy()
    if cert:
        env["GIT_SSL_CERT"] = cert[0]
        env["GIT_SSL_KEY"] = cert[1]
    if isinstance(verify, str):
        env["GIT_SSL_CAINFO"] = verify
    elif not verify:
        env["GIT_SSL_NO_VERIFY"] = "1"
    # Disable credential helpers so git never prompts for credentials.
    # Auth is embedded directly in the clone URL as git:<token>@.
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    env["GIT_CONFIG_COUNT"] = str(count + 2)
    env["GIT_CONFIG_KEY_%d" % count] = "credential.helper"
    env["GIT_CONFIG_VALUE_%d" % count] = ""
    env["GIT_CONFIG_KEY_%d" % (count + 1)] = "http.useNetrc"
    env["GIT_CONFIG_VALUE_%d" % (count + 1)] = "false"
    return env


def _git_clone_url(base_url, project_path_with_namespace, token):
    """Build an HTTPS git URL with credentials embedded as git:<token>@hostname.

    The username 'git' is used instead of 'oauth2' because GitLab instances
    fronted by Keycloak OIDC may validate 'oauth2' tokens as JWTs and reject
    PATs.  Using 'git' as the username bypasses that path.
    """
    parsed = urlparse(base_url)
    netloc = parsed.hostname
    if parsed.port:
        netloc += ":%d" % parsed.port
    if token:
        netloc = "git:%s@%s" % (token, netloc)
    path = "/%s.git" % project_path_with_namespace.lstrip("/")
    return urlunparse((parsed.scheme, netloc, path, "", "", ""))


def _run_git(args, env, cwd=None):
    result = subprocess.run(
        ["git"] + args,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result


def _bundle_project(project_path, clone_url, git_env):
    """Mirror-clone a project and return its git bundle bytes, or None if the repo is empty."""
    _log("  cloning %s ..." % project_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        mirror_dir = os.path.join(tmpdir, "mirror")
        result = _run_git(["clone", "--mirror", clone_url, mirror_dir], git_env)
        if result.returncode != 0:
            raise GitLabDRError(
                "git clone failed for %s: %s" % (project_path, result.stderr.decode(errors="replace").strip())
            )
        # Check for at least one ref — empty repos cannot be bundled
        ref_check = _run_git(["for-each-ref", "--count=1"], git_env, cwd=mirror_dir)
        if not ref_check.stdout.strip():
            _log("  skipping %s (empty repository)" % project_path)
            return None
        bundle_path = os.path.join(tmpdir, "repo.bundle")
        result = _run_git(["bundle", "create", bundle_path, "--all"], git_env, cwd=mirror_dir)
        if result.returncode != 0:
            raise GitLabDRError(
                "git bundle create failed for %s: %s" % (project_path, result.stderr.decode(errors="replace").strip())
            )
        with open(bundle_path, "rb") as fh:
            data = fh.read()
        _log("  bundled  %s (%.1f MB)" % (project_path, len(data) / 1024 / 1024))
        return data


def _push_bundle(bundle_bytes, push_url, git_env):
    """Restore a git bundle by pushing all refs to a remote URL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_path = os.path.join(tmpdir, "repo.bundle")
        with open(bundle_path, "wb") as fh:
            fh.write(bundle_bytes)
        clone_dir = os.path.join(tmpdir, "clone")
        result = _run_git(["clone", "--mirror", bundle_path, clone_dir], git_env)
        if result.returncode != 0:
            raise GitLabDRError("git clone from bundle failed: %s" % result.stderr.decode(errors="replace").strip())
        result = _run_git(["push", "--mirror", push_url], git_env, cwd=clone_dir)
        if result.returncode != 0:
            raise GitLabDRError("git push failed: %s" % result.stderr.decode(errors="replace").strip())


def _iter_repo_bundles(backup_data, base_url, token, cert=None, verify=True, report=None):
    """Yield (archive_name, bundle_bytes) for every non-empty project repo in backup_data."""
    git_env = _git_env(cert=cert, verify=verify, token=token)
    stack = list(backup_data.get("groups", []))
    while stack:
        group_data = stack.pop(0)
        for subgroup in group_data.get("subgroups", []):
            stack.append(subgroup)
        for project_data in group_data.get("projects", []):
            details = project_data.get("details", {})
            full_path = details.get("path_with_namespace") or details.get("path", "unknown")
            clone_url = _git_clone_url(base_url, full_path, token)
            try:
                bundle_bytes = _bundle_project(full_path, clone_url, git_env)
            except GitLabDRError as exc:
                msg = "skipping repo %s: %s" % (full_path, exc)
                if report is not None:
                    report.warn(msg)
                else:
                    _log("warning: " + msg)
                continue
            if bundle_bytes:
                yield "repos/%s.bundle" % full_path, bundle_bytes


def _make_bundle_supplier(archive_path, password=None):
    """Return a callable that reads a project's bundle from the archive by full path."""

    def supplier(full_path):
        archive_name = "repos/%s.bundle" % full_path
        try:
            if password:
                with pyzipper.AESZipFile(archive_path, mode="r") as archive:
                    archive.setpassword(password.encode("utf-8"))
                    return archive.read(archive_name)
            with zipfile.ZipFile(archive_path, mode="r") as archive:
                return archive.read(archive_name)
        except KeyError:
            return None

    return supplier


def _fetch_variables_with_sudo(client, project_id, full_path, details, report=None):
    """Try to fetch project variables by sudoing as creator, then as first owner/maintainer."""
    log = report.info if report else _log
    warn = report.warn if report else lambda m: _log("warning: " + m)
    # Access levels: 50=owner, 40=maintainer
    candidates = []
    creator_id = details.get("creator_id")
    if creator_id:
        candidates.append(("creator", creator_id))
    try:
        members = client.project_members(project_id)
        for member in members:
            if member.get("access_level", 0) >= 40:
                candidates.append(("member", member["id"]))
    except GitLabDRError:
        pass
    for label, uid in candidates:
        log("  403 on variables for %s, retrying with sudo=%s (%s) ..." % (full_path, uid, label))
        try:
            return client.project_variables(project_id, sudo=uid)
        except GitLabDRError:
            continue
    archived = details.get("archived", False)
    reason = " (project is archived)" if archived else " (exhausted sudo candidates)"
    warn("cannot fetch variables for %s%s" % (full_path, reason))
    return []


def _collect_project_data(client, project, report=None):
    log = report.info if report else _log
    project_id = project["id"]
    full_path = project.get("path_with_namespace") or project.get("path", str(project_id))
    log("  collecting project %s ..." % full_path)
    details = client.project_details(project_id)
    try:
        variables = client.project_variables(project_id)
    except GitLabDRError as exc:
        if "403" in str(exc):
            variables = _fetch_variables_with_sudo(client, project_id, full_path, details, report=report)
        else:
            raise
    return {
        "details": details,
        "variables": variables,
        "merge_requests": client.project_merge_requests(project_id),
    }


def _collect_group_data(client, group, children_map=None, report=None):
    log = report.info if report else _log
    group_id = group["id"]
    full_path = group.get("full_path") or group.get("path", str(group_id))
    log("collecting group %s ..." % full_path)
    projects = client.group_projects(group_id)
    if children_map is not None:
        subgroups = children_map.get(group_id, [])
    else:
        subgroups = client.list_subgroups(group_id)
    return {
        "details": group,
        "variables": client.group_variables(group_id),
        "members": client.group_members(group_id),
        "projects": [_collect_project_data(client, project, report=report) for project in projects],
        "subgroups": [_collect_group_data(client, child, children_map, report=report) for child in subgroups],
    }


def build_backup(client, group_path=None, report=None):
    log = report.info if report else _log
    log("starting backup ...")
    if group_path:
        log("fetching group %s ..." % group_path)
        root = client.get_group(group_path)
        if root is None:
            raise GitLabDRError("Group not found: %s" % group_path)
        return {
            "schema_version": 1,
            "groups": [_collect_group_data(client, root, report=report)],
        }
    log("fetching all groups ...")
    groups = client.list_groups()
    children_map = {}
    top_level = []
    for group in groups:
        parent_id = group.get("parent_id")
        if parent_id is None:
            top_level.append(group)
            continue
        children_map.setdefault(parent_id, []).append(group)
    return {
        "schema_version": 1,
        "groups": [_collect_group_data(client, group, children_map, report=report) for group in top_level],
    }


def _restore_merge_requests(client, project_id, backup_merge_requests):
    existing = client.project_merge_requests(project_id, state="opened")
    existing_keys = {(mr.get("title"), mr.get("source_branch"), mr.get("target_branch")) for mr in existing}
    for mr in backup_merge_requests:
        key = (mr.get("title"), mr.get("source_branch"), mr.get("target_branch"))
        if not all(key) or key in existing_keys:
            continue
        try:
            client.create_merge_request(project_id, key[0], key[1], key[2])
        except GitLabDRError:
            continue


def _restore_project(client, namespace_id, namespace_path, project_data, bundle_supplier=None, report=None):
    details = project_data["details"]
    full_path = details.get("path_with_namespace") or "%s/%s" % (namespace_path, details["path"])
    _log("  restoring project %s ..." % full_path)
    project = client.project_exists(namespace_path, details["path"])
    if project is None:
        project = client.create_project(
            namespace_id=namespace_id,
            name=details["name"],
            path=details["path"],
            visibility=details.get("visibility"),
        )
    project_id = project["id"]
    for variable in project_data.get("variables", []):
        if "key" in variable and "value" in variable:
            client.upsert_project_variable(project_id, variable)
    _restore_merge_requests(client, project_id, project_data.get("merge_requests", []))
    if bundle_supplier:
        full_path = (
            details.get("path_with_namespace")
            or project.get("path_with_namespace")
            or "%s/%s" % (namespace_path, details["path"])
        )
        bundle_bytes = bundle_supplier(full_path)
        if bundle_bytes:
            push_url = _git_clone_url(client.base_url, project.get("path_with_namespace", full_path), client.token)
            git_env = _git_env(cert=client.cert, verify=client.verify, token=client.token)
            _log("  pushing repo  %s ..." % full_path)
            # Temporarily unprotect all protected branches so force-push succeeds
            try:
                protected = client.list_protected_branches(project_id)
            except GitLabDRError:
                protected = []
            for pb in protected:
                try:
                    client.unprotect_branch(project_id, pb["name"])
                except GitLabDRError:
                    pass
            try:
                _push_bundle(bundle_bytes, push_url, git_env)
                _log("  pushed        %s" % full_path)
            except GitLabDRError as exc:
                msg = "failed to push repo %s: %s" % (full_path, exc)
                if report is not None:
                    report.warn(msg)
                else:
                    _log("warning: " + msg)
            finally:
                # Re-protect branches that were unprotected
                for pb in protected:
                    try:
                        push_lvl = (pb.get("push_access_levels") or [{}])[0].get("access_level", 40)
                        merge_lvl = (pb.get("merge_access_levels") or [{}])[0].get("access_level", 40)
                        client.protect_branch(project_id, pb["name"], push_lvl, merge_lvl)
                    except GitLabDRError:
                        pass


def _restore_group(client, group_data, parent=None, bundle_supplier=None, report=None):
    details = group_data["details"]
    full_path = details.get("full_path") or details.get("path", "?")
    _log("restoring group %s ..." % full_path)
    group = client.get_group(details["full_path"])
    if group is None:
        group = client.create_group(
            name=details["name"],
            path=details["path"],
            parent_id=parent["id"] if parent else None,
            visibility=details.get("visibility"),
        )
    group_id = group["id"]
    group_path = group.get("full_path") or details.get("full_path")

    for variable in group_data.get("variables", []):
        if "key" in variable and "value" in variable:
            client.upsert_group_variable(group_id, variable)

    for project in group_data.get("projects", []):
        _restore_project(client, group_id, group_path, project, bundle_supplier=bundle_supplier, report=report)

    for subgroup in group_data.get("subgroups", []):
        _restore_group(client, subgroup, parent=group, bundle_supplier=bundle_supplier, report=report)


def restore_backup(client, backup_data, bundle_supplier=None, report=None):
    for group in backup_data.get("groups", []):
        _restore_group(client, group, bundle_supplier=bundle_supplier, report=report)


def _checkout_project_files(project_path, clone_url, git_env, dest_dir):
    """Clone a project and copy the working tree (no .git dir) to dest_dir.

    Returns True if files were written, False if the repository was empty.
    """
    _log("  cloning %s ..." % project_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = os.path.join(tmpdir, "clone")
        result = _run_git(["clone", clone_url, clone_dir], git_env)
        if result.returncode != 0:
            raise GitLabDRError(
                "git clone failed for %s: %s" % (project_path, result.stderr.decode(errors="replace").strip())
            )
        ls = _run_git(["ls-files"], git_env, cwd=clone_dir)
        if not ls.stdout.strip():
            _log("  skipping %s (empty repository)" % project_path)
            return False
        os.makedirs(dest_dir, exist_ok=True)
        for item in os.listdir(clone_dir):
            if item == ".git":
                continue
            src = os.path.join(clone_dir, item)
            dst = os.path.join(dest_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        file_count = len(ls.stdout.decode(errors="replace").strip().splitlines())
        _log("  checked out %s (%d file(s))" % (project_path, file_count))
        return True


def _write_repo_files_to_dir(backup_data, base_url, token, dest_dir, cert=None, verify=True, report=None):
    """Clone all project repos and write working tree files to dest_dir/repos/<full_path>/.

    Each project is stored as plain files rather than a git bundle, making the
    directory fully readable and transformable by text-processing tools such as
    xsyncfar.  Git history is NOT preserved — see --repos-as-files for details.
    """
    git_env = _git_env(cert=cert, verify=verify, token=token)
    stack = list(backup_data.get("groups", []))
    while stack:
        group_data = stack.pop(0)
        for subgroup in group_data.get("subgroups", []):
            stack.append(subgroup)
        for project_data in group_data.get("projects", []):
            details = project_data.get("details", {})
            full_path = details.get("path_with_namespace") or details.get("path", "unknown")
            clone_url = _git_clone_url(base_url, full_path, token)
            project_dest = os.path.join(dest_dir, "repos", full_path)
            try:
                _checkout_project_files(full_path, clone_url, git_env, project_dest)
            except GitLabDRError as exc:
                msg = "skipping repo %s: %s" % (full_path, exc)
                if report is not None:
                    report.warn(msg)
                else:
                    _log("warning: " + msg)


def _make_bundle_from_dir(source_dir, git_env):
    """Create git bundle bytes from a plain files directory as a single initial commit.

    This is used during restore of --repos-as-files backups to push the plain
    files back to GitLab as a fresh repository.  All git history from the
    original repository is lost.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "repo")
        shutil.copytree(source_dir, repo_dir)
        env = git_env.copy()
        env.setdefault("GIT_AUTHOR_NAME", "gitlab-dr")
        env.setdefault("GIT_AUTHOR_EMAIL", "gitlab-dr@localhost")
        env.setdefault("GIT_COMMITTER_NAME", "gitlab-dr")
        env.setdefault("GIT_COMMITTER_EMAIL", "gitlab-dr@localhost")
        _run_git(["init"], env, cwd=repo_dir)
        _run_git(["symbolic-ref", "HEAD", "refs/heads/main"], env, cwd=repo_dir)
        _run_git(["add", "."], env, cwd=repo_dir)
        result = _run_git(
            ["commit", "-m", "Restored by gitlab-dr (--repos-as-files): git history not preserved"],
            env,
            cwd=repo_dir,
        )
        if result.returncode != 0:
            return None
        bundle_path = os.path.join(tmpdir, "repo.bundle")
        result = _run_git(["bundle", "create", bundle_path, "--all"], env, cwd=repo_dir)
        if result.returncode != 0:
            raise GitLabDRError("git bundle create failed: %s" % result.stderr.decode(errors="replace").strip())
        with open(bundle_path, "rb") as fh:
            return fh.read()


def _make_files_supplier_dir(dir_path, git_env):
    """Return a bundle supplier that builds a single-commit bundle from a plain-files directory.

    Used during restore of --repos-as-files backups.  Each project is pushed as
    a fresh repository with a single commit — git history from the original
    source is not preserved.
    """

    def supplier(full_path):
        project_dir = os.path.join(dir_path, "repos", full_path)
        if not os.path.isdir(project_dir):
            return None
        return _make_bundle_from_dir(project_dir, git_env)

    return supplier


def write_backup_dir(dir_path, backup_data, repo_bundles_iter=None):
    """Write a backup to a directory instead of a zip archive."""
    os.makedirs(dir_path, exist_ok=True)
    backup_json_path = os.path.join(dir_path, "backup.json")
    with open(backup_json_path, "w", encoding="utf-8") as fh:
        json.dump(backup_data, fh, indent=2, sort_keys=True)
    if repo_bundles_iter:
        for name, data in repo_bundles_iter:
            bundle_path = os.path.join(dir_path, name)
            os.makedirs(os.path.dirname(bundle_path), exist_ok=True)
            with open(bundle_path, "wb") as fh:
                fh.write(data)


def read_backup_dir(dir_path):
    """Read a backup from a directory produced by write_backup_dir."""
    backup_json_path = os.path.join(dir_path, "backup.json")
    if not os.path.isfile(backup_json_path):
        raise GitLabDRError("No backup.json found in directory: %s" % dir_path)
    with open(backup_json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _make_bundle_supplier_dir(dir_path):
    """Return a callable that reads a project's bundle from a backup directory."""

    def supplier(full_path):
        bundle_path = os.path.join(dir_path, "repos", full_path + ".bundle")
        if not os.path.isfile(bundle_path):
            return None
        with open(bundle_path, "rb") as fh:
            return fh.read()

    return supplier


def write_backup_archive(path, backup_data, repo_bundles_iter=None, encrypt=False, password=None):
    payload = json.dumps(backup_data, indent=2, sort_keys=True).encode("utf-8")
    if encrypt:
        if not password:
            raise GitLabDRError("Password is required when encryption is enabled")
        with pyzipper.AESZipFile(
            path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as archive:
            archive.setpassword(password.encode("utf-8"))
            archive.setencryption(pyzipper.WZ_AES, nbits=256)
            archive.writestr("backup.json", payload)
            if repo_bundles_iter:
                for name, data in repo_bundles_iter:
                    archive.writestr(name, data)
        return

    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", payload)
        if repo_bundles_iter:
            for name, data in repo_bundles_iter:
                archive.writestr(name, data)


def archive_is_encrypted(path):
    with zipfile.ZipFile(path, mode="r") as archive:
        infos = archive.infolist()
        if not infos:
            raise GitLabDRError("Backup archive is empty")
        return bool(infos[0].flag_bits & 0x1)


def read_backup_archive(path, password=None):
    if archive_is_encrypted(path):
        if not password:
            raise GitLabDRError("Password is required to read encrypted backup")
        with pyzipper.AESZipFile(path, mode="r") as archive:
            archive.setpassword(password.encode("utf-8"))
            with archive.open("backup.json", mode="r") as handle:
                return json.load(handle)

    with zipfile.ZipFile(path, mode="r") as archive:
        with archive.open("backup.json", mode="r") as handle:
            return json.load(handle)
