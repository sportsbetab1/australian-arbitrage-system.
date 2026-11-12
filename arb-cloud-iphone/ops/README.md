# Recovery and scheduled operations

## Host preflight and acceptance

Run `python -m scripts.preflight` with the host's private environment loaded.
It opens the existing database read-only, checks the schema, quick integrity,
foreign keys, active accounts, secure-cookie configuration, and the newest
local snapshot's integrity and age (maximum 26 hours). It prints sanitized JSON
and exits nonzero for failed checks. Warnings identify optional disabled
integrations and operations that cannot be verified locally. `ok: true` means
these local checks passed; it is not production approval. It does not prove
provider access, backup content equivalence, off-site protection or delivery.
Run it after account setup and the first backup, and include it in host
monitoring. Missing databases are reported without being created.

With development requirements installed, `python -m scripts.smoke_local` starts
a temporary server on localhost with generated test-only credentials and a
disposable database. It verifies private access, setup/sign-in, rounded-stake
qualification, a fictional position and settlement, P/L, backup and logout.
It shuts the server down and removes its data afterward. It never uses the
operating database or calls a live odds/push/bookmaker service. This command is
also run by Linux CI. Rehearse real-host HTTPS, proxy configuration, sign-in,
backups and phone delivery separately before opening access.

## Request budgets and proxy trust

Authentication POSTs share a 60-request/minute global budget and a
20-request/minute client budget. Both setup and sign-in count, including
invalid inputs. Authenticated odds requests share a 10-request/minute budget
per user. Counters are serialized in SQLite across workers, expire after each
fixed minute, and store hashed identities rather than raw IP addresses. A 429
response includes `Retry-After` and is not cached. Fixed windows can allow a
burst across a minute boundary. These limits supplement account lockout;
configure edge connection/body limits and monitoring as well.

Client identity and request scheme come from the ASGI scope. Configure
Uvicorn's `--forwarded-allow-ips` with the actual trusted reverse proxy addresses
on the selected host, and make the application port inaccessible directly.
Do not use `*` unless the platform guarantees only its trusted proxy can reach
the port. Raw forwarded headers do not override origin checks inside the app.
An incorrect proxy configuration can group clients into one budget or reject
legitimate HTTPS requests; verify it on the chosen host.

These templates are prepared for a Linux host with systemd. They are not
installed or enabled by the application. No hosting service is provisioned.
Both jobs must run on the same host/volume as the application database; a
separate Render cron service cannot assume access to the web service disk.

## Scheduler setup

Adapt `User`, `WorkingDirectory`, and `ExecStart` in the four unit files to the
actual application installation. Create the `arbitrage` service account first.
Create `/etc/arbitrage.env` outside the checkout, owned by root with mode 600.
Use the same `DB_PATH`, `BACKUP_DIR`, `BACKUP_KEEP`, and VAPID settings as the
application. The service account needs read/write access to the database
directory and backup destination. Do not put credentials in unit files.

After reviewing the paths, the host administrator can install these four files
into `/etc/systemd/system/`, run `systemctl daemon-reload`, then enable:

```bash
sudo systemctl enable --now arbitrage-backup.timer arbitrage-push.timer
sudo systemctl start arbitrage-backup.service arbitrage-push.service
systemctl list-timers 'arbitrage-*'
journalctl -u arbitrage-backup.service -u arbitrage-push.service --since today
```

The backup timer runs daily at 03:00 UTC and catches up after downtime. The
push timer waits a minute after the previous invocation finishes, so the timer
does not overlap its own worker. Check service failures and backup age through
the host's monitoring system. Missing VAPID credentials disable actual delivery;
a successful worker exit does not mean notifications reached a device.
Windows users can schedule the same module commands with Task Scheduler using
the application's Python, working directory, and private host environment.

## Isolated restore drill

Create a snapshot, then run the drill against its returned path:

```bash
.venv/bin/python -m scripts.backup_db
.venv/bin/python -m scripts.recovery_drill /path/to/arbitrage-snapshot.sqlite
```

The drill restores into a temporary directory and verifies SQLite integrity,
foreign keys, all table counts, a logical SQL digest, and the pre-restore safety
copy. It checks that the snapshot's logical content is unchanged. Only counts
and a digest are printed, never account/session contents. It never restores
over the operating database. A failure exits nonzero. Retain the JSON output
with the date and application commit in an administrator-only recovery record.
This checks database recovery; rehearse application startup and sign-in on an
isolated host separately before production use.

## Actual recovery

1. Stop the web application, push worker, backup timer, and every other database
   writer. Confirm processes exited. Preserve the database and journal files.
2. If `-wal`, `-shm`, or `-journal` files remain, have the administrator open and
   cleanly close/checkpoint the stopped database using SQLite. Never delete
   journals to force a restore: they may hold committed transactions. The
   restore script refuses targets with journal files present. This guard does
   not prove that no writer is running; stopping writers is still required.
3. Verify the selected snapshot with the isolated drill above.
4. Run `python -m scripts.restore_db /path/to/snapshot.sqlite --db /path/to/database`.
   The command retains an integrity-checked pre-restore safety snapshot.
5. Start the application in isolation first, verify health, account access,
   balances, open legs, settlements, and ledger totals against known records.
   Restore can resurrect old sessions and pending notifications; revoke both
   users' sessions with `scripts.manage_auth revoke-sessions` and inspect the
   push outbox before re-enabling workers. Then resume normal access and timers.

## Off-site copies

Local snapshots protect against logical mistakes, not loss of the host/volume.
Choose a private encrypted remote destination and configure a host-managed
transfer after each successful backup. Preserve remote retention independently
of local pruning, alert on failed transfers, and periodically download a remote
copy and run the isolated drill. No remote destination or credentials are
configured here; off-site protection remains incomplete until that is done.
On POSIX systems new snapshot and restore files use mode 600. On Windows,
restrict the database/backup directories with NTFS ACLs.
