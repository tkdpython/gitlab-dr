import argparse
import getpass
import os
import sys

from .core import (
    GitLabClient,
    GitLabDRError,
    RunReport,
    _git_env,
    _iter_repo_bundles,
    _log,
    _make_bundle_supplier,
    _make_bundle_supplier_dir,
    _make_files_supplier_dir,
    _write_repo_files_to_dir,
    archive_is_encrypted,
    build_backup,
    read_backup_archive,
    read_backup_dir,
    restore_backup,
    write_backup_archive,
    write_backup_dir,
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gitlab_dr",
        description="Backup and restore GitLab groups and projects.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backup", action="store_true", help="Create a backup archive.")
    mode.add_argument("--restore", action="store_true", help="Restore from backup archive.")

    parser.add_argument(
        "--gitlab-url",
        default=os.getenv("GITLAB_DR_URL"),
        required=not os.getenv("GITLAB_DR_URL"),
        help="GitLab instance URL (or set GITLAB_DR_URL).",
    )
    backup_target = parser.add_mutually_exclusive_group(required=True)
    backup_target.add_argument("--backup-file", help="Path to the backup archive (.zip) file.")
    backup_target.add_argument(
        "--backup-dir",
        help="Path to a directory for uncompressed backup. Encryption is not available in this mode.",
    )
    parser.add_argument("--token", default=os.getenv("GITLAB_DR_TOKEN"), help="GitLab admin PAT token.")
    parser.add_argument(
        "--encrypt",
        action="store_true",
        help="Encrypt backup archive with AES-256 (--backup-file only).",
    )
    parser.add_argument(
        "--exclude-repo-clone",
        action="store_true",
        default=False,
        help="Exclude git repository contents from the backup/restore. By default, repos are included.",
    )
    parser.add_argument(
        "--repos-as-files",
        action="store_true",
        default=False,
        help=(
            "Store repository contents as plain files instead of git bundles (--backup-dir only). "
            "WARNING: git history is NOT preserved. On restore, each project is created with a "
            "single initial commit. Use this mode when you need to post-process the files with "
            "text transformation tools (e.g. xsyncfar) before restoring to a different environment."
        ),
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
    client_cert = os.path.expanduser(args.client_cert) if args.client_cert else None
    client_key = os.path.expanduser(args.client_key) if args.client_key else None
    ca_cert = os.path.expanduser(args.ca_cert) if args.ca_cert else None
    cert = (client_cert, client_key) if client_cert and client_key else None
    verify = ca_cert if ca_cert else True
    return GitLabClient(url, token=args.token, cert=cert, verify=verify)


def run_backup(args):
    report = RunReport()
    client = _build_client(args.gitlab_url, args)
    backup_data = build_backup(client, group_path=args.group, report=report)
    repo_bundles_iter = None
    if args.backup_dir and args.repos_as_files:
        warning = (
            "WARNING: --repos-as-files is set. Repository contents will be stored as plain "
            "files. Git history will NOT be preserved. On restore, each project will be "
            "created with a single initial commit."
        )
        _log(warning)
        report.warn(warning)
        _log("checking out repository files ...")
        write_backup_dir(args.backup_dir, backup_data)
        _write_repo_files_to_dir(
            backup_data,
            base_url=client.base_url,
            token=client.token,
            dest_dir=args.backup_dir,
            cert=client.cert,
            verify=client.verify,
            report=report,
        )
        _log("backup complete: %s" % args.backup_dir)
        report.print_summary()
        log_path = os.path.join(args.backup_dir, "backup.log")
        report.write_log(log_path)
        _log("log written: %s" % log_path)
        return 0
    if not args.exclude_repo_clone:
        _log("bundling repositories ...")
        repo_bundles_iter = _iter_repo_bundles(
            backup_data,
            base_url=client.base_url,
            token=client.token,
            cert=client.cert,
            verify=client.verify,
            report=report,
        )
    if args.backup_dir:
        _log("writing backup to directory %s ..." % args.backup_dir)
        write_backup_dir(args.backup_dir, backup_data, repo_bundles_iter=repo_bundles_iter)
        _log("backup complete: %s" % args.backup_dir)
        report.print_summary()
        log_path = os.path.join(args.backup_dir, "backup.log")
        report.write_log(log_path)
        _log("log written: %s" % log_path)
    else:
        password = resolve_password(args.encrypt, "backup")
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
    client = _build_client(args.gitlab_url, args)
    bundle_supplier = None
    if args.backup_dir:
        _log("reading backup from directory %s ..." % args.backup_dir)
        backup_data = read_backup_dir(args.backup_dir)
        if not args.exclude_repo_clone:
            if args.repos_as_files:
                warning = (
                    "WARNING: --repos-as-files is set. Repositories will be restored from plain "
                    "files as a single initial commit. Git history from the original source will "
                    "NOT be present in the restored projects."
                )
                _log(warning)
                report.warn(warning)
                git_env = _git_env(cert=client.cert, verify=client.verify)
                bundle_supplier = _make_files_supplier_dir(args.backup_dir, git_env)
            else:
                bundle_supplier = _make_bundle_supplier_dir(args.backup_dir)
    else:
        encrypted = args.encrypt
        if not encrypted:
            try:
                encrypted = archive_is_encrypted(args.backup_file)
            except Exception:
                encrypted = False
        password = resolve_password(encrypted, "restore")
        _log("reading archive %s ..." % args.backup_file)
        backup_data = read_backup_archive(args.backup_file, password=password)
        if not args.exclude_repo_clone:
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
        if args.backup_dir and args.encrypt:
            print("error: --encrypt is not supported with --backup-dir", file=sys.stderr)
            return 1
        if args.repos_as_files and not args.backup_dir:
            print("error: --repos-as-files requires --backup-dir", file=sys.stderr)
            return 1
        if args.repos_as_files and args.exclude_repo_clone:
            print("error: --repos-as-files and --exclude-repo-clone are mutually exclusive", file=sys.stderr)
            return 1
        if args.backup:
            return run_backup(args)
        return run_restore(args)
    except GitLabDRError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
