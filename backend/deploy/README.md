# Server notes (Oracle Linux VM, repo at /opt/adelaide-transit)

`transit-api`, `transit-collector` are systemd services (already installed,
`Restart=always`). `systemd/` holds the extra config applied on 2026-10-03 so a
slow request can no longer starve the whole 1GB VM:

| file | install to |
|---|---|
| `transit-api.service.d-limits.conf` | `/etc/systemd/system/transit-api.service.d/limits.conf` |
| `transit-collector.service.d-limits.conf` | `/etc/systemd/system/transit-collector.service.d/limits.conf` |
| `sshd.service.d-priority.conf` | `/etc/systemd/system/sshd.service.d/priority.conf` |
| `journald-transit.conf` | `/etc/systemd/journald.conf.d/transit.conf` |
| `sysstat-collect.timer.d-1min.conf` | `/etc/systemd/system/sysstat-collect.timer.d/1min.conf` |

Then `sudo systemctl daemon-reload`, restart the units, `sudo systemctl restart systemd-journald`,
`sudo dnf install -y sysstat` and enable `sysstat sysstat-collect.timer`.

History for diagnosing a hang next time: `journalctl -b -1` (persistent) and `sar -u -r -q -f /var/log/sa/saDD`.

## Data retention

Raw `realtime_updates` is kept forever on purpose (ML training data). The
`transit-cleanup.timer` on the server (8-week delete, `retention_cleanup.py`)
is deliberately left disabled, do not enable it. `app/cleanup.py` also deletes
raw rows, so do not schedule it either.

## One-off: covering indexes

`python -m app.add_indexes` (run from `backend/`) builds two covering indexes on
`realtime_updates`. It holds the write lock for minutes, so it only runs in the
collector's quiet hours (01:00-05:00). It is not in `init_db()` on purpose.
