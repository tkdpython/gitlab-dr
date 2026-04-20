import argparse
import getpass
import os
import sys

from .core import (
    GitLabClient,
    GitLabDRError,
    RunReport,
    _iter_repo_bundles,
    _log,
    _make_bundle_supplier,
    archive_is_encrypted,
    build_backup,
    read_backup_archive,
    restore_backup,
    write_backup_archive,
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gitlab_dr",
        description="Backup and restore GitLab groups and projects.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backup", action="store_true", help="Create a backup archive.")
    mode.add_argument("--restore", action="store_true", help="Restore from backup archive.")

    parser.add_argument("--gitlab-url", required=True, help="GitLab instance URL.")
    parser.add_argument("--backup-file", required=True, help="Path to the backup archive file.")
    parser.add_argument("--token", default=os.getenv("GITLAB_DR_TOKEN"), help="GitLab admin PAT token.")
    parser.add_argument("--encrypt", action="store_true", help="Encrypt backup archive with AES-256.")
    parser.add_argument(
        "--include-repos",
        action="store_true",
        default=False,
        help="Include git repository contents as bundles in the archive (requires git on PATH).",
    )
    parser.add_argument(
        "--group",
        default=None,
        help="Scope backup/restore to a specific group by full path (e.g. 'mygroup' or 'mygroup/subgroup').",
    )
    parser.add_argument(
        "--client-cert",
        default=os.getenv("GITLAB_DR_CLIENT_CERT"),
        help="Path to PEM client certificate for mTLS.",
    )
    parser.add_argument(
        "--client-key",
        default=os.getenv("GITLAB_DR_CLIENT_KEY"),
        help="Path to PEM client key for mTLS.",
    )
    parser.add_argument(
        "--ca-cert",
        default=os.getenv("GITLAB_DR_CA_CERT"),
        help="Path to custom CA certificate bundle (optional).",
    )
    return parser


def resolve_password(encrypt_enabled, action_label):
    if not encrypt_enabled:
        return None
    password = os.getenv("GITLAB_DR_PASSWORD")
    if password:
        return password
    prompt = "Enter password for %s: " % action_label
    confirm_prompt = "Confirm password: "
    first = getpass.getpass(prompt)
    if action_label == "backup":
        second = getpass.getpass(confirm_prompt)
        if first != second:
            raise GitLabDRError("Passwords do not match")
    if not first:
        raise GitLabDRError("Password cannot be empty")
    return first


def _build_client(url, args):
    if not args.token:
        raise GitLabDRError("GitLab token is required (--token or GITLAB_DR_TOKEN)")
    if bool(args.client_cert) != bool(args.client_key):
        raise GitLabDRError("Both --client-cert and --client-key must be provided together")
    cert = (args.client_cert, args.client_key) if args.client_cert and args.client_key else None
    verify = args.ca_cert if args.ca_cert else True
    return GitLabClient(url, token=args.token, cert=cert, verify=verify)


def run_backup(args):
    report = RunReport()
    password = resolve_password(args.encrypt, "backup")
    client = _build_client(args.gitlab_url, args)
    backup_data = build_backup(client, group_path=args.group, report=report)
    repo_bundles_iter = None
    if args.include_repos:
        _log("bundling repositories ...")
        repo_bundles_iter = _iter_repo_bundles(
            backup_data,
            base_url=client.base_url,
            token=client.token,
            cert=client.cert,
            verify=client.verify,
            report=report,
        )
    _log("writing archive %s ..." % args.backup_file)
    write_backup_archive(
        args.backup_file,
        backup_data,
        repo_bundles_iter=repo_bundles_iter,
        encrypt=args.encrypt,
        password=password,
    )
    _log("backup complete: %s" % args.backup_file)
    report.print_summary()
    log_path = os.path.splitext(args.backup_file)[0] + ".log"
    report.write_log(log_path)
    _log("log written: %s" % log_path)
    return 0


def run_restore(args):
    report = RunReport()
    encrypted = args.encrypt
    if not encrypted:
        try:
            encrypted = archive_is_encrypted(args.backup_file)
        except Exception:
            encrypted = False
    password = resolve_password(encrypted, "restore")
    _log("reading archive %s ..." % args.backup_file)
    backup_data = read_backup_archive(args.backup_file, password=password)
    client = _build_client(args.gitlab_url, args)
    bundle_supplier = None
    if args.include_repos:
        bundle_supplier = _make_bundle_supplier(args.backup_file, password=password if encrypted else None)
    _log("starting restore ...")
    restore_backup(client, backup_data, bundle_supplier=bundle_supplier, report=report)
    _log("restore complete")
    report.print_summary()
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.backup:
            return run_backup(args)
        return run_restore(args)
    except GitLabDRError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
