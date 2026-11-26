# First run on Windows

One-time setup of a workstation or server. About 30 minutes plus the first SEC load
(roughly 1 to 2 hours for full history across ~150 REITs).

## 1. Install

1. **Python 3.12** from python.org. Tick "Add python.exe to PATH".
2. **MySQL 8.4 LTS** (Community Server MSI) from dev.mysql.com. Note the root password.
3. **Git for Windows**.

## 2. Get the code

```powershell
cd C:\
git clone https://github.com/jaymoore74/reit-street.git
cd reit-street
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -c constraints.txt -e .
```

## 3. Database and user (MySQL command line client, as root)

```sql
CREATE DATABASE reitstreet CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'reit'@'localhost' IDENTIFIED BY 'pick-a-long-password';
GRANT ALL ON reitstreet.* TO 'reit'@'localhost';
```

## 4. Configure

```powershell
copy config\config.example.ini config.ini
notepad config.ini
setx REITSTREET_DB_PASSWORD "pick-a-long-password"
```

In `config.ini`, set **`[sec] user_agent` to your own name and email**, for example
`user_agent = Jane Smith jane@yourfirm.com`. SEC requires it and blocks anonymous traffic;
the pipeline refuses to call SEC while the placeholder is there. Set `[paths] universe_csv`
to the universe file. Open a new PowerShell window after `setx`, then activate the venv again.

## 5. Load

```powershell
reitstreet config          # check settings; secrets show as ***
scripts\windows\first_run.bat
```

`first_run.bat` runs `migrate`, `universe`, `sec-sync --full-history`, `dq`, `spotcheck` and
`status`. If the load is interrupted, resume it:

```powershell
reitstreet status                       # note the run_id of the sec-sync
reitstreet sec-sync --full-history --resume <run_id>
```

## 6. Check against the filings

`reitstreet spotcheck --tickers PLD,O,SPG` prints latest FY revenue, net income, total
assets, debt and FFO with the accession number and filing link. Open two or three and
compare against the 10-K.

## 7. Schedule

From an admin Command Prompt in the repo folder: `scripts\windows\register_tasks.bat`.
That registers the weekday sync every 30 minutes (1 PM to 8 PM), the Sunday full refresh
and the hourly monitor. The PC must be on; for production move to an always-on host.
