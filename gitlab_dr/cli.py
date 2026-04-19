import argparse
import getpass
import os
import sys

from .core import (
    GitLabClient,
    GitLabDRError,
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

    parser.add_argument("--source", required=True, help="Source URL (backup) or archive path (restore).")
    parser.add_argument(
        "--destination", required=True, help="Destination archive path (backup) or URL (restore)."
    )
    parser.add_argument("--token", default=os.getenv("GITLAB_DR_TOKEN"), help="GitLab admin PAT token.")
    parser.add_argument("--encrypt", action="store_true", help="Encrypt backup archive with AES-256.")
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
    password = resolve_password(args.encrypt, "backup")
    client = _build_client(args.source, args)
    backup_data = build_backup(client)
    write_backup_archive(args.destination, backup_data, encrypt=args.encrypt, password=password)
    return 0


def run_restore(args):
    encrypted = args.encrypt
    if not encrypted:
        try:
            encrypted = archive_is_encrypted(args.source)
        except Exception:
            encrypted = False
    password = resolve_password(encrypted, "restore")
    backup_data = read_backup_archive(args.source, password=password)
    client = _build_client(args.destination, args)
    restore_backup(client, backup_data)
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
