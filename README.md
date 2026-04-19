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

- `GITLAB_DR_TOKEN`: GitLab admin PAT (used if `--token` is not provided)
- `GITLAB_DR_PASSWORD`: password used for encrypted backup/restore archives
- `GITLAB_DR_CLIENT_CERT`: PEM client certificate path for mTLS
- `GITLAB_DR_CLIENT_KEY`: PEM client key path for mTLS
- `GITLAB_DR_CA_CERT`: optional CA bundle path

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
- Group and project CI/CD variables
- Project merge requests
- Group member listings
- Git repository contents (all branches, tags, and refs) — when `--include-repos` is used

Restore recreates missing groups/projects and reapplies variables and merge requests where possible. When `--include-repos` is used on restore, each project's git history is pushed to the target instance via `git push --mirror`. Failures on individual repositories are reported as warnings and do not abort the rest of the restore.

Empty repositories (no commits) are silently skipped during bundle creation.
