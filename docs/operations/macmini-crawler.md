# Mac mini crawler operations

The only scheduled crawler is launchd job `com.loum.roeum-crawler` on Mac mini.
It runs `/Users/loum/loum/roeum-crawler/run_daily.sh` daily at 00:00 KST.
GitHub runs PR tests only; its former Crawl Update workflow is removed.
The Monday daily run also executes nodong.kr, preserving the former GitHub-only
weekly source. `run_weekly.sh` is a manual seven-day catch-up, not a second schedule.

## Execution and exclusion

Daily, manual weekly and deployment commands use the same POSIX advisory lock
`/tmp/roeum_crawler.lock`. Exit 75 means another operation owns the lock.
Never delete this lock file: the lock is released by the OS, not file deletion.
The lock descriptor is inherited by batch children, retaining exclusion if the
coordinator terminates before them. Direct `crawl.py`, Prefect and raw git pull
bypass this lock and must not be used while the scheduled job can run.

Each batch log records deployed SHA, Python version, dirty-source state and final
exit code. Dirty tracked source prevents execution; the machine-owned .env is
excluded. Crawl/refresh/nodong failures propagate to launchd even if notifications
succeed. Existing crawl retries remain three attempts with 15-minute delay.
Default concurrency remains 3; CRAWLER_CONCURRENT may be set by the operator after
resource measurement. No embedding service or database migration is modified.

## Reviewed commit deployment

After tests and review, merge the PR into main. On Mac mini run:

```bash
cd /Users/loum/loum/roeum-crawler
/Users/loum/miniconda3/envs/crawler/bin/python scripts/ops/macmini_batch.py --deploy FULL_40_CHARACTER_COMMIT_SHA
```

The command locks out crawling, fetches origin/main, verifies the target belongs
to main and is a fast-forward from the checkout, and preserves local .env.
It refuses dependency or .env changes: those require a maintenance window with
the launchd job unloaded, dependencies validated, and the job restored afterward.
Logs record the deployed revision. The batch never pulls unreviewed main on its own.

For first installation only, with launchd unloaded and no legacy crawl process
running, fast-forward the existing main checkout to the reviewed merge SHA.
Then test the lock and shell syntax, reload the existing launchd plist.
Retire the GitHub Crawl Update workflow and verify no hosted crawl is running.

## Verification without crawling or MongoDB writes

```bash
/Users/loum/miniconda3/envs/crawler/bin/python scripts/ops/macmini_batch.py --check-lock
/Users/loum/miniconda3/envs/crawler/bin/python -m pytest tests/test_macmini_batch.py -q
bash -n run_daily.sh run_weekly.sh scripts/ops/run_batch.sh
launchctl list com.loum.roeum-crawler
```

Do not mark the next scheduled crawl successful until its log reports the
expected revision and exit=0. File existence of the lock does not mean busy.
