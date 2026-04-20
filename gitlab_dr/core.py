import json
import os
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

    def warn(self, msg):
        _log("warning: " + msg)
        self.warnings.append(msg)

    def print_summary(self):
        _log("")
        if not self.warnings:
            _log("summary: completed with no warnings")
            return
        _log("summary: %d warning(s):" % len(self.warnings))
        for w in self.warnings:
            _log("  - " + w)


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


def _git_env(cert=None, verify=True):
    """Return a subprocess environment dict with git SSL settings applied."""
    env = os.environ.copy()
    if cert:
        env["GIT_SSL_CERT"] = cert[0]
        env["GIT_SSL_KEY"] = cert[1]
    if isinstance(verify, str):
        env["GIT_SSL_CAINFO"] = verify
    elif not verify:
        env["GIT_SSL_NO_VERIFY"] = "1"
    return env


def _git_clone_url(base_url, project_path_with_namespace, token):
    """Build an authenticated HTTPS git clone URL."""
    parsed = urlparse(base_url)
    netloc = "oauth2:%s@%s" % (token, parsed.hostname)
    if parsed.port:
        netloc += ":%d" % parsed.port
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
    git_env = _git_env(cert=cert, verify=verify)
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


def _collect_project_data(client, project):
    project_id = project["id"]
    full_path = project.get("path_with_namespace") or project.get("path", str(project_id))
    _log("  collecting project %s ..." % full_path)
    details = client.project_details(project_id)
    try:
        variables = client.project_variables(project_id)
    except GitLabDRError as exc:
        if "403" in str(exc):
            creator_id = details.get("creator_id")
            if creator_id:
                _log("  403 on variables for %s, retrying with sudo=%s ..." % (full_path, creator_id))
                try:
                    variables = client.project_variables(project_id, sudo=creator_id)
                except GitLabDRError as exc2:
                    _log("warning: cannot fetch variables for %s even with sudo: %s" % (full_path, exc2))
                    variables = []
            else:
                _log("warning: cannot fetch variables for %s (no creator_id for sudo): %s" % (full_path, exc))
                variables = []
        else:
            raise
    return {
        "details": details,
        "variables": variables,
        "merge_requests": client.project_merge_requests(project_id),
    }


def _collect_group_data(client, group, children_map=None):
    group_id = group["id"]
    full_path = group.get("full_path") or group.get("path", str(group_id))
    _log("collecting group %s ..." % full_path)
    projects = client.group_projects(group_id)
    if children_map is not None:
        subgroups = children_map.get(group_id, [])
    else:
        subgroups = client.list_subgroups(group_id)
    return {
        "details": group,
        "variables": client.group_variables(group_id),
        "members": client.group_members(group_id),
        "projects": [_collect_project_data(client, project) for project in projects],
        "subgroups": [_collect_group_data(client, child, children_map) for child in subgroups],
    }


def build_backup(client, group_path=None):
    _log("starting backup ...")
    if group_path:
        _log("fetching group %s ..." % group_path)
        root = client.get_group(group_path)
        if root is None:
            raise GitLabDRError("Group not found: %s" % group_path)
        return {
            "schema_version": 1,
            "groups": [_collect_group_data(client, root)],
        }
    _log("fetching all groups ...")
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
        "groups": [_collect_group_data(client, group, children_map) for group in top_level],
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
            git_env = _git_env(cert=client.cert, verify=client.verify)
            _log("  pushing repo  %s ..." % full_path)
            try:
                _push_bundle(bundle_bytes, push_url, git_env)
                _log("  pushed        %s" % full_path)
            except GitLabDRError as exc:
                msg = "failed to push repo %s: %s" % (full_path, exc)
                if report is not None:
                    report.warn(msg)
                else:
                    _log("warning: " + msg)


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
