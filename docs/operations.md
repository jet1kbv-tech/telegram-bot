# Production operations

The production checkout is `/root/telegram-bot`, and persistent data is stored at
`/root/telegram-bot-data/data.json`. Deployments create timestamped
`deploy-data-*.json` backups in `/root/telegram-bot-data/backups` before updating
the application.

## Roll back application code

1. Identify and record the previous known-good commit SHA.
2. On the production server, fetch the repository and move the checkout to that
   exact commit:

   ```sh
   cd /root/telegram-bot
   git fetch origin main
   git reset --hard <known-good-commit-sha>
   /root/telegram-bot/venv/bin/python -m pip install -r requirements.txt
   /root/telegram-bot/venv/bin/python -m compileall -q bot main.py
   systemctl restart telegram-bot.service
   systemctl is-active --quiet telegram-bot.service
   ```

3. Treat a nonzero compile, restart, or active-service check as a failed rollback
   and investigate before declaring recovery complete.

**Do not automatically restore data when rolling back code unless the data itself
is known to be corrupted or incompatible.** Code rollback and data restoration
are separate operations.

## Restore production data

Choose the backup explicitly; do not assume that the newest file is the correct
recovery point.

```sh
find /root/telegram-bot-data/backups -maxdepth 1 -type f \
  -name 'deploy-data-*.json' -printf '%f\n' | sort

systemctl stop telegram-bot.service
timestamp=$(date -u +%Y%m%dT%H%M%S%NZ)
cp -- /root/telegram-bot-data/data.json \
  "/root/telegram-bot-data/backups/pre-restore-data-${timestamp}.json"
cp -- "/root/telegram-bot-data/backups/<selected-deploy-backup>.json" \
  /root/telegram-bot-data/data.json
systemctl start telegram-bot.service
systemctl is-active --quiet telegram-bot.service
```

Stop the bot before replacing the data file so that it cannot write during the
restore. Always make the `pre-restore-data-*` safety copy first. If the current
data file is missing, record that fact and investigate rather than omitting the
safety step without review.
