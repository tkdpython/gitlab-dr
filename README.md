# gitlab-dr

`gitlab-dr` is a Python package and CLI for GitLab disaster recovery backup and restore operations.

## Requirements

- Python `>=3.6`
- GitLab admin Personal Access Token (PAT)
- Network access to the source/destination GitLab instances

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
  --source https://gitlab.source.example \
  --destination /path/to/backup.zip \
  --token "$GITLAB_DR_TOKEN"
```

Encrypted backup (AES-256):

```bash
gitlab_dr \
  --backup \
  --source https://gitlab.source.example \
  --destination /path/to/backup.zip \
  --encrypt
```

When `--encrypt` is set, the CLI prompts for a password unless `GITLAB_DR_PASSWORD` is already set.

### Restore

```bash
gitlab_dr \
  --restore \
  --source /path/to/backup.zip \
  --destination https://gitlab.target.example \
  --token "$GITLAB_DR_TOKEN"
```

### mTLS support

```bash
gitlab_dr \
  --backup \
  --source https://gitlab.source.example \
  --destination /path/to/backup.zip \
  --client-cert /path/to/client.crt.pem \
  --client-key /path/to/client.key.pem
```

## Backup scope

The backup captures recursively discovered groups/subgroups and contained projects, including:

- Group and project metadata
- Group and project CI/CD variables
- Project merge requests
- Group member listings

Restore recreates missing groups/projects and reapplies variables and merge requests where possible.
