# TODO

- Add retry/backoff behavior for transient GitLab API failures and rate limiting.
- Add optional inclusion/exclusion filters for groups/projects by path.
- Add dry-run mode for restore preview.
- Add richer restore support for additional settings (branch protections, webhooks, approvals, runners).
- Add integration tests against a disposable GitLab test instance.
- Add signed release process and provenance attestation checks for publishing.
- Add LFS support for `--include-repos` (`git lfs fetch --all` before bundling).
