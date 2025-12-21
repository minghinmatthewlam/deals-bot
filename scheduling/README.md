# Scheduling Deal Intelligence

This directory contains configuration files for automated daily execution.

## macOS (launchd)

### Setup

1. **Create logs directory:**
   ```bash
   mkdir -p /path/to/deal-intel/logs
   ```

2. **Copy and customize the plist:**
   ```bash
   # Copy template
   cp scheduling/com.dealintel.daily.plist ~/Library/LaunchAgents/

   # Edit with your actual path
   # Replace all instances of /path/to/deal-intel with your actual path
   nano ~/Library/LaunchAgents/com.dealintel.daily.plist
   ```

3. **Load the job:**
   ```bash
   launchctl load ~/Library/LaunchAgents/com.dealintel.daily.plist
   ```

4. **Verify it's loaded:**
   ```bash
   launchctl list | grep dealintel
   ```

### Manual Test

```bash
# Trigger immediately for testing
launchctl start com.dealintel.daily

# Check logs
tail -f /path/to/deal-intel/logs/launchd.log
```

### Unload

```bash
launchctl unload ~/Library/LaunchAgents/com.dealintel.daily.plist
```

### Troubleshooting

```bash
# Validate plist syntax
plutil -lint ~/Library/LaunchAgents/com.dealintel.daily.plist

# Check recent runs
log show --predicate 'subsystem == "com.apple.xpc.launchd"' --last 1h | grep dealintel

# View logs
cat /path/to/deal-intel/logs/launchd.log
cat /path/to/deal-intel/logs/launchd.err
```

---

## Linux (cron)

### Setup

1. **Create logs directory:**
   ```bash
   mkdir -p /path/to/deal-intel/logs
   ```

2. **Edit crontab:**
   ```bash
   crontab -e
   ```

3. **Add entry (run daily at 10:00 AM):**
   ```cron
   # Deal Intelligence - Daily digest
   0 10 * * * cd /path/to/deal-intel && .venv/bin/dealintel run >> logs/cron.log 2>&1
   ```

4. **Verify:**
   ```bash
   crontab -l
   ```

### With Environment Variables

If you need to load environment variables:

```cron
0 10 * * * cd /path/to/deal-intel && source .env && .venv/bin/dealintel run >> logs/cron.log 2>&1
```

Or create a wrapper script:

```bash
#!/bin/bash
# /path/to/deal-intel/run-daily.sh
cd /path/to/deal-intel
source .venv/bin/activate
source .env
dealintel run >> logs/cron.log 2>&1
```

Then in crontab:
```cron
0 10 * * * /path/to/deal-intel/run-daily.sh
```

### Log Rotation

Add to `/etc/logrotate.d/dealintel`:

```
/path/to/deal-intel/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
```

---

## Dry Run Testing

Before enabling scheduled execution, test with dry-run mode:

```bash
cd /path/to/deal-intel
.venv/bin/dealintel run --dry-run

# Check the preview
open digest_preview.html
```

---

## Timezone Considerations

The scheduler runs based on system time. Deal Intelligence uses Eastern Time (ET) internally for digest date tracking, ensuring:

- Same-day idempotency regardless of when the job runs
- Consistent "today's deals" regardless of timezone

For servers in different timezones, adjust the schedule hour accordingly.
