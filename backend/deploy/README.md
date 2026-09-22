# Deploy (Oracle Linux VM)

Run once, after SSH access works. Paths assume the repo is cloned at
`/home/opc/adelaide-transit-predictor` with a venv at `backend/.venv`,
adjust the `.service` files first if your layout differs.

```bash
cd ~/adelaide-transit-predictor
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

sudo cp backend/deploy/transit-api.service /etc/systemd/system/
sudo cp backend/deploy/transit-collector.service /etc/systemd/system/
sudo cp backend/deploy/transit-cleanup.service /etc/systemd/system/
sudo cp backend/deploy/transit-cleanup.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp backend/deploy/journald-transit-limits.conf /etc/systemd/journald.conf.d/

sudo systemctl daemon-reload
sudo systemctl restart systemd-journald
sudo systemctl enable --now transit-api transit-collector transit-cleanup.timer

# verify
systemctl status transit-api transit-collector transit-cleanup.timer
systemctl list-timers transit-cleanup.timer
df -h /
```

## Why this exists

`backend/scripts/run_cleanup.ps1` only ever ran on the original Windows
PC setup (`.venv\Scripts\Activate.ps1`, PowerShell), it has no effect on
the Oracle Linux VM, there is no Linux cron/systemd equivalent for it. So
since the move to the VM, `realtime_updates` and the journal log have
both grown unbounded, the repeated disk-full crashes are that, not a
one-off bug.

`transit-cleanup.timer` runs `python -m app.cleanup --keep-days 7` daily,
same script, same 7-day raw-row retention, correct here (`VACUUM` also
runs each time so the `.db` file actually shrinks, not just the row
count). The journald drop-in caps total log disk usage at 200MB
regardless of what any service prints, so a chatty process can't repeat
the "No space left on device" crash on its own.
