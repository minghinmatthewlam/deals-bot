# Deal Intelligence - Troubleshooting Runbook

This document provides troubleshooting guidance for common issues with Deal Intelligence.

---

## Quick Diagnostics

```bash
# Check system status
.venv/bin/dealintel status

# View recent runs
make db-shell
# Then: SELECT * FROM runs ORDER BY started_at DESC LIMIT 10;
```

---

## Common Issues

### 1. No Emails Matched

**Symptom:** Emails are ingested but `matched=0` in run stats.

**Cause:** Sender addresses not in stores.yaml.

**Solution:**

```sql
-- Find unmatched senders (run in db-shell)
SELECT from_address, from_domain, COUNT(*)
FROM emails_raw
WHERE store_id IS NULL
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT 20;
```

Then add the domains/addresses to `stores.yaml`:

```yaml
stores:
  - slug: new-store
    name: New Store
    sources:
      - type: gmail_from_address
        pattern: deals@newstore.com
        priority: 100
      - type: gmail_from_domain
        pattern: newstore.com
        priority: 50
```

Run `make seed` to apply.

---

### 2. Gmail History ID Expired (404 Error)

**Symptom:** Log shows "History ID expired, doing full sync"

**Cause:** The Gmail history ID expires after ~7 days of inactivity. This is normal and handled automatically.

**What happens:** The system falls back to a full sync of the last 14 days. No action needed.

**To verify:**

```sql
SELECT * FROM gmail_state;
-- Check last_full_sync_at timestamp
```

---

### 3. Empty Digest

**Symptom:** Pipeline runs successfully but no digest is generated or sent.

**Cause:** No NEW or UPDATED promos since last digest.

**Diagnose:**

```sql
-- Check for recent changes
SELECT s.name, p.headline, pc.change_type, pc.changed_at
FROM promo_changes pc
JOIN promos p ON p.id = pc.promo_id
JOIN stores s ON s.id = p.store_id
ORDER BY pc.changed_at DESC
LIMIT 20;

-- Check last digest time
SELECT digest_date_et, digest_sent_at
FROM runs
WHERE run_type = 'daily_digest' AND digest_sent_at IS NOT NULL
ORDER BY digest_sent_at DESC
LIMIT 1;
```

**If changes exist but weren't included:** Check if promos have `status='active'`.

---

### 4. Extraction Errors

**Symptom:** Extractions failing, emails marked with `extraction_status='error'`

**Diagnose:**

```sql
SELECT id, subject, extraction_status, extraction_error
FROM emails_raw
WHERE extraction_status = 'error'
ORDER BY created_at DESC
LIMIT 10;
```

**Common causes:**

1. **OpenAI API key invalid:**
   - Check `.env` for valid `OPENAI_API_KEY`
   - Verify key has credits at platform.openai.com

2. **Rate limiting:**
   - The system uses exponential backoff (3 retries)
   - Persistent failures indicate account issues

3. **Email content issues:**
   - Very long emails may hit token limits
   - HTML-heavy emails may parse poorly

**Retry failed extractions:**

```sql
UPDATE emails_raw
SET extraction_status = 'pending', extraction_error = NULL
WHERE extraction_status = 'error';
```

Then run `make run`.

---

### 5. Duplicate Digest (Should Never Happen)

**Symptom:** Multiple digests sent for the same day.

**Protection:** The `UNIQUE(run_type, digest_date_et)` constraint prevents this.

**If you see duplicate attempts:**

```sql
SELECT digest_date_et, COUNT(*)
FROM runs
WHERE run_type = 'daily_digest'
GROUP BY 1
HAVING COUNT(*) > 1;
```

This would only happen if the constraint was removed or if runs are in different time zones.

---

### 6. SendGrid Errors

**Symptom:** Digest generated but not sent.

**Diagnose:**
- Check logs for "SendGrid error"
- Verify `SENDGRID_API_KEY` in `.env`
- Check SendGrid dashboard for bounces/blocks

**Common issues:**

1. **Sender not verified:** Verify sender email in SendGrid dashboard
2. **Rate limiting:** Free tier = 100 emails/day
3. **Invalid API key:** Regenerate key if compromised

**Workaround:** Use dry-run mode to generate preview:

```bash
make run-dry
open digest_preview.html
```

---

### 7. Database Connection Issues

**Symptom:** "Connection refused" or similar database errors.

**Check Docker:**

```bash
docker compose ps
# Should show dealintel-db as "Up (healthy)"
```

**Restart database:**

```bash
make db-down
make db-up
```

**Check connection:**

```bash
make db-shell
# Should connect to psql
```

---

### 8. Concurrent Run Blocked

**Symptom:** Log shows "Another run in progress, exiting"

**Cause:** Advisory lock held by another process.

**Check for running processes:**

```bash
ps aux | grep dealintel
```

**If no processes and lock seems stuck:**

```sql
-- Check advisory locks (requires superuser)
SELECT * FROM pg_locks WHERE locktype = 'advisory';
```

Normally, advisory locks are automatically released when the session ends. If a process crashed, disconnect all sessions and reconnect.

---

## Useful SQL Queries

### Recent Runs Summary

```sql
SELECT
  digest_date_et,
  status,
  (stats_json->>'promos_created')::int AS new,
  (stats_json->>'promos_updated')::int AS updated,
  digest_sent_at IS NOT NULL AS sent
FROM runs
ORDER BY started_at DESC
LIMIT 10;
```

### Promo Inventory by Store

```sql
SELECT
  s.name,
  COUNT(*) AS promo_count,
  COUNT(*) FILTER (WHERE p.status = 'active') AS active,
  MAX(p.last_seen_at) AS last_seen
FROM promos p
JOIN stores s ON s.id = p.store_id
GROUP BY s.name
ORDER BY promo_count DESC;
```

### Email Extraction Status

```sql
SELECT
  extraction_status,
  COUNT(*) AS count
FROM emails_raw
GROUP BY 1;
```

### Find Promos by Code

```sql
SELECT s.name, p.headline, p.code, p.ends_at
FROM promos p
JOIN stores s ON s.id = p.store_id
WHERE p.code ILIKE '%SAVE%'
ORDER BY p.last_seen_at DESC;
```

---

## Log Locations

| Platform | Location |
|----------|----------|
| macOS launchd | `logs/launchd.log`, `logs/launchd.err` |
| Linux cron | `logs/cron.log` |
| Manual runs | stdout/stderr |

---

## Getting Help

1. Check this runbook first
2. Review logs for specific error messages
3. Check database state with SQL queries above
4. If stuck, the source code in `src/dealintel/` is well-documented

---

## Maintenance Tasks

### Clean Up Old Data

```sql
-- Delete emails older than 90 days (optional)
DELETE FROM emails_raw
WHERE created_at < NOW() - INTERVAL '90 days';

-- Archive old runs
DELETE FROM runs
WHERE started_at < NOW() - INTERVAL '180 days';
```

### Refresh Stores

After updating `stores.yaml`:

```bash
make seed
```

This is safe to run multiple times (upserts).

### Force Re-extraction

To re-extract all emails for a store:

```sql
UPDATE emails_raw
SET extraction_status = 'pending', extraction_error = NULL
WHERE store_id = (SELECT id FROM stores WHERE slug = 'store-slug');
```

Then run `make run` or `make run-dry`.
