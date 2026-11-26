# Backup and restore

Two things make up the system of record: the MySQL database and the raw store
(`[paths] data_dir`, `raw` folder). Back up both. The raw store alone can rebuild
staging and the warehouse (`replay`), but not operations history or manual mapping rules.

## Backup (nightly)

```powershell
$d = Get-Date -Format yyyyMMdd
mysqldump --single-transaction --routines --triggers -u reit -p --result-file="D:\backup\reitstreet_$d.sql" reitstreet
robocopy C:\reit-street\data\raw D:\backup\raw /E /XO
```

Raw objects never change, so the copy is incremental. Keep database dumps for 30 days
and one per month for a year. Copy both off the machine.

## Restore

```sql
CREATE DATABASE reitstreet CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```
```powershell
cmd /c "mysql -u reit -p reitstreet < D:\backup\reitstreet_YYYYMMDD.sql"
robocopy D:\backup\raw C:\reit-street\data\raw /E
reitstreet migrate          # brings the schema to the code's revision
reitstreet dq --only contract
```

## Test the restore

Quarterly, restore into a scratch database (`reitstreet_restore`) and run `dq`.
A backup that has never been restored is not a backup.
