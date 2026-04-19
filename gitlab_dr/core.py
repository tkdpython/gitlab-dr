import json
import zipfile
from urllib.parse import quote

import pyzipper
import requests


class GitLabDRError(Exception):
    pass


class GitLabClient(object):
    def __init__(self, base_url, token, cert=None, verify=True, timeout=30):
        self.base_url = base_url.rstrip("/")
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
            raise GitLabDRError(
                "GitLab API %s %s failed: %s %s"
                % (method, path, response.status_code, response.text)
            )
        if response.status_code == 204:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def list_paginated(self, path, params=None):
        page = 1
        all_items = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        while True:
            params["page"] = page
            response = self.session.get(
                self._url(path), params=params, timeout=self.timeout
            )
            if response.status_code != 200:
                raise GitLabDRError(
                    "GitLab API GET %s failed: %s %s"
                    % (path, response.status_code, response.text)
                )
            items = response.json()
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

    def group_projects(self, group_id):
        return self.list_paginated(
            "/groups/%s/projects" % group_id,
            params={"include_subgroups": False, "simple": False},
        )

    def project_details(self, project_id):
        return self._request("GET", "/projects/%s" % project_id)

    def project_variables(self, project_id):
        return self.list_paginated("/projects/%s/variables" % project_id)

    def project_merge_requests(self, project_id):
        return self.list_paginated(
            "/projects/%s/merge_requests" % project_id,
            params={"state": "all"},
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

    def list_project_merge_requests(self, project_id):
        return self.list_paginated(
            "/projects/%s/merge_requests" % project_id,
            params={"state": "opened"},
        )

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


def _collect_project_data(client, project):
    project_id = project["id"]
    return {
        "details": client.project_details(project_id),
        "variables": client.project_variables(project_id),
        "merge_requests": client.project_merge_requests(project_id),
    }


def _collect_group_data(client, group, children_map):
    group_id = group["id"]
    projects = client.group_projects(group_id)
    return {
        "details": group,
        "variables": client.group_variables(group_id),
        "members": client.group_members(group_id),
        "projects": [_collect_project_data(client, project) for project in projects],
        "subgroups": [
            _collect_group_data(client, child, children_map)
            for child in children_map.get(group_id, [])
        ],
    }


def build_backup(client):
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
    existing = client.list_project_merge_requests(project_id)
    existing_keys = {
        (mr.get("title"), mr.get("source_branch"), mr.get("target_branch"))
        for mr in existing
    }
    for mr in backup_merge_requests:
        key = (mr.get("title"), mr.get("source_branch"), mr.get("target_branch"))
        if not all(key) or key in existing_keys:
            continue
        try:
            client.create_merge_request(project_id, key[0], key[1], key[2])
        except GitLabDRError:
            continue


def _restore_project(client, namespace_id, namespace_path, project_data):
    details = project_data["details"]
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


def _restore_group(client, group_data, parent=None):
    details = group_data["details"]
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
        _restore_project(client, group_id, group_path, project)

    for subgroup in group_data.get("subgroups", []):
        _restore_group(client, subgroup, parent=group)


def restore_backup(client, backup_data):
    for group in backup_data.get("groups", []):
        _restore_group(client, group)


def write_backup_archive(path, backup_data, encrypt=False, password=None):
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
        return

    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", payload)


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
