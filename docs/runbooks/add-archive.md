# Import an archive of older supplementals

```powershell
reitstreet import-archive D:\supplementals
reitstreet parse-docs
reitstreet dq
```

Files are de-duplicated by SHA-256, so importing the same folder twice adds nothing.
Ticker and period come from the path and file name, for example
`PLD\2019\PLD_Q3_2019_Supplemental.pdf` or `pld-3q19-supp.pdf`. Anything that cannot be
identified is stored with status `needs_review`.

For full control, give a manifest CSV (paths relative to the folder):

```
path,ticker,period_end,published_date,doc_kind
PLD/2019/q3.pdf,PLD,2019-09-30,2019-10-16,supplemental
```

```powershell
reitstreet import-archive D:\supplementals --manifest D:\supplementals\manifest.csv
```
