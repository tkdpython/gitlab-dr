# gitlab-dr

`gitlab-dr` is a Python package and CLI for GitLab disaster recovery backup and restore operations.

## Requirements

- Python `>=3.6`
- GitLab admin Personal Access Token (PAT)
- Network access to the source/destination GitLab instances
- `git` on `PATH` (required only when `--include-repos` is used)

## Install

```bash
pip install gitlab-dr
```

## CLI execution

```bash
gitlab_dr --help
python -m gitlab_dr --help
```

## Authentication and environment variables

All options can be supplied via environment variables instead of CLI flags. Tilde (`~`) expansion is supported in all path values.

| Variable | CLI flag | Description |
|---|---|---|
| `GITLAB_DR_URL` | `--gitlab-url` | GitLab instance URL |
| `GITLAB_DR_TOKEN` | `--token` | GitLab admin PAT |
| `GITLAB_DR_PASSWORD` | *(prompted)* | Password for encrypted archives |
| `GITLAB_DR_CLIENT_CERT` | `--client-cert` | PEM client certificate path (mTLS) |
| `GITLAB_DR_CLIENT_KEY` | `--client-key` | PEM client key path (mTLS) |
| `GITLAB_DR_CA_CERT` | `--ca-cert` | Custom CA bundle path |

`GITLAB_DR_URL` and `GITLAB_DR_TOKEN` are required (either as env vars or CLI flags). `GITLAB_DR_PASSWORD` is read automatically when `--encrypt` is set, falling back to an interactive prompt if not set.

## Usage

### Backup

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --token "$GITLAB_DR_TOKEN"
```

Scope to a specific group or nested subgroup:

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --group my-group

gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --group my-group/sub-group
```

Include full git repository contents (`git clone --mirror` + bundle):

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --include-repos
```

Encrypted backup (AES-256):

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --encrypt
```

When `--encrypt` is set, the CLI prompts for a password unless `GITLAB_DR_PASSWORD` is already set.

### Restore

```bash
gitlab_dr \
  --restore \
  --gitlab-url https://gitlab.target.example.com \
  --backup-file /path/to/backup.zip \
  --token "$GITLAB_DR_TOKEN"
```

Restore including git repository contents:

```bash
gitlab_dr \
  --restore \
  --gitlab-url https://gitlab.target.example.com \
  --backup-file /path/to/backup.zip \
  --include-repos
```

### mTLS support

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --client-cert /path/to/client.crt.pem \
  --client-key /path/to/client.key.pem
```

When `--include-repos` is used alongside mTLS, the client certificate and key are passed to `git` via `GIT_SSL_CERT` and `GIT_SSL_KEY` environment variables automatically.

To trust a custom CA:

```bash
gitlab_dr \
  --backup \
  --gitlab-url https://gitlab.example.com \
  --backup-file /path/to/backup.zip \
  --ca-cert /path/to/ca-bundle.pem
```

## Backup scope

The backup captures recursively discovered groups/subgroups and contained projects, including:

- Group and project metadata
- Group and project CI/CD variables (including masked values — store the archive securely)
- Project merge requests
- Group member listings
- Git repository contents (all branches, tags, and refs) — when `--include-repos` is used

### CI/CD variable access

An admin PAT returns unmasked CI/CD variable values. If a project returns 403 for variables (common on archived projects or projects where the creator account has been removed), the tool automatically retries using `Sudo` impersonation — first as the project `creator_id`, then as each current owner/maintainer. If all candidates are exhausted the project is skipped with a warning rather than aborting the run.

> ⚠️ The backup archive will contain plaintext secrets. Use `--encrypt` and protect the output file appropriately.

### Run log

After every backup a `.log` file is written alongside the archive (e.g. `backup.zip` → `backup.log`) containing the full run transcript including all warnings. The terminal summary lists only warnings; the log file contains everything.

### Restore behaviour

Restore recreates missing groups/projects and reapplies variables and merge requests where possible. When `--include-repos` is used on restore, each project's git history is pushed to the target instance via `git push --mirror`. Failures on individual repositories are reported as warnings and do not abort the rest of the restore.

Empty repositories (no commits) are silently skipped during bundle creation.
