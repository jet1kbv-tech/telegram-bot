# Production operations

The production checkout is `/root/telegram-bot`, and persistent data is stored at
`/root/telegram-bot-data/data.json`. Deployments create timestamped
`deploy-data-*.json` backups in `/root/telegram-bot-data/backups` before updating
the application.

## Release flow

For normal development, merge or push to `main`. GitHub Actions runs the full
pytest suite and compiles `bot` and `main.py`; production is **not** changed
automatically.

To release to production:

1. Confirm that CI is green for the commit to release.
2. Either run the **CI and Manual Production Deploy** workflow from GitHub
   Actions, or follow the manual server deployment procedure below.
3. Verify that `telegram-bot.service` is healthy and record the deployed commit.

Automatic SSH deployment on pushes was intentionally disabled because the
GitHub-hosted runner could not establish TCP connectivity to production SSH.
Direct operator SSH continues to work.

## Deploy manually from the production server

After connecting to the server as an authorized operator, run:

```sh
set -euo pipefail

data_file=/root/telegram-bot-data/data.json
backup_dir=/root/telegram-bot-data/backups
mkdir -p "$backup_dir"
if [ -f "$data_file" ]; then
  timestamp=$(date -u +%Y%m%dT%H%M%S%NZ)
  cp -- "$data_file" "$backup_dir/deploy-data-${timestamp}.json"
fi

mapfile -t deploy_backups < <(
  find "$backup_dir" -maxdepth 1 -type f -name 'deploy-data-*.json' -printf '%f\n' | sort
)
excess=$((${#deploy_backups[@]} - 30))
for ((index = 0; index < excess; index++)); do
  rm -- "$backup_dir/${deploy_backups[$index]}"
done

cd /root/telegram-bot
git pull --ff-only origin main
/root/telegram-bot/venv/bin/python -m pip install -r requirements.txt
/root/telegram-bot/venv/bin/python -m compileall -q bot main.py
systemctl restart telegram-bot.service
systemctl is-active --quiet telegram-bot.service
git rev-parse HEAD
```

Any failed command stops the deployment. The backup retention command only
removes older `deploy-data-*.json` files and leaves unrelated backups intact.
There is no automatic data rollback.

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
