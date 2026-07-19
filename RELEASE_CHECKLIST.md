# Index Inbox v1.0.0 release checklist

This checklist promotes `v1.0.0-rc.1` only after the exact release candidate has passed production-like validation on Unraid. Record the tested commit and backup archive before merging or tagging.

## 1. Record the candidate

- [ ] Record the release-candidate commit with `git rev-parse HEAD`.
- [ ] Confirm the branch is `release/v1.0.0-rc1` and the working tree contains no deployment-specific files.
- [ ] Confirm the Browser tests GitHub Actions check is green for that commit.

## 2. Back up production

- [ ] Create a verified backup from **Storage, backup & export**.
- [ ] Run `docker exec index-inbox flask backup verify /data/backups/ARCHIVE_NAME.zip`.
- [ ] Copy the verified archive off the application data volume.
- [ ] Record the archive filename, entry count, audio count, and verification result.

## 3. Deploy without replacing data

- [ ] Confirm `INDEX_DATA_PATH` still points at the production directory.
- [ ] Pull `release/v1.0.0-rc1` and rebuild with `docker compose up -d --build --force-recreate`.
- [ ] Confirm the header shows `v1.0.0-rc.1`.
- [ ] Confirm `curl http://127.0.0.1:5050/health` returns `{"ok":true}`.
- [ ] Inspect `docker compose logs --tail=100 index-inbox` for migration or startup errors.

## 4. Authentication and access

- [ ] Sign in through the Cloudflare HTTPS URL.
- [ ] Sign out and sign back in with the local owner account.
- [ ] Confirm the direct LAN route behaves as intended for the configured secure-cookie policy.
- [ ] Confirm no trusted-proxy `.env` variables are required when using the secure default.

## 5. Critical regression

- [ ] Receive a standalone webhook note and see its live notice without refreshing.
- [ ] Create a temporary voice group and receive an exactly matched grouped note.
- [ ] Produce a near-match, accept its suggestion, and confirm no alias is learned.
- [ ] Rename, archive, and reopen the temporary group.
- [ ] Edit its timeline, use **Save & Back**, and confirm the main inbox refreshes.
- [ ] Download and inspect the group Markdown, JSON, and ZIP exports.
- [ ] Remove the temporary group while preserving its entries, then remove test entries if desired.

## 6. Backup and restore regression

- [ ] Create and download a post-deployment verified backup.
- [ ] Verify its manifest with the CLI.
- [ ] Restore it into a new staging directory and disposable localhost-only container.
- [ ] Confirm staging health, users, groups, entry count, and available audio.
- [ ] Stop and remove the staging container and staging directory.

## 7. Promotion and rollback readiness

- [ ] Record the previous known-good main commit and keep its verified backup available.
- [ ] Confirm rollback means redeploying that commit with the unchanged production data path; never overwrite `/data` during a code rollback.
- [ ] Change the displayed version from `v1.0.0-rc.1` to `v1.0.0` only after every required item above passes.
- [ ] Merge the release pull request, tag the merge commit `v1.0.0`, and publish release notes.
