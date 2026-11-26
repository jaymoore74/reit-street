# Running REIT Street on a Mac, with your own model

This is the exact list of what to do, in order, from buying the Mac to running a model
you trained on our own data. Every command is copied from what the repo does today.
Facts about hardware and models were checked on 2026-11-26; recheck prices on the day
you buy.

What you end up with:
- the whole pipeline (SEC, IR fetcher, parser, property master, warehouse) running on
  the Mac against a MySQL on the Mac, on a schedule, with no AWS needed
- a local model that reads supplemental pages into numbers and answers questions such as
  "give me everything on NNN" from the warehouse
- a repeatable way to fine-tune that model on our data and prove it got better before
  you use it

Time: about 2 hours of setup on day 1, a first data load that runs overnight, then a
first training run of a few hours.

---

## 1. Buy the right Mac

Memory is the one spec that decides what you can run. It cannot be upgraded later.

| | Recommended | Minimum that still works | Desktop option |
|---|---|---|---|
| Machine | MacBook Pro 14" or 16", **M5 Max with 40-core GPU** | same chip | Mac Studio M5 Max |
| Memory | **128 GB** | 64 GB | 128 GB |
| Storage | 2 TB (4 TB if you keep many models) | 2 TB | 2 TB |
| Price (reported, Aug-Nov 2026) | about $6,699 (14", 2 TB) | about $5,499 (derived, not quoted) | about $5,099 |
| What it can do | serve and train the primary model | serve the primary model, run the smoke recipe; cannot train the primary model | same as recommended, $1,600 less, not portable |

Why 128 GB: training the recommended model (Qwen3.8-27B at 8-bit, LoRA rank 32 on 16 of
64 layers) peaked at 57.3 GB in a published run. Add macOS, MySQL, a browser and your
other apps, and 64 GB is not enough. Only the 40-core-GPU M5 Max offers 128 GB in a
laptop, and it also has the fastest memory (614 GB/s vs 460 GB/s on the 32-core chip),
which is what sets how fast a model answers.

Not worth paying for: the OLED ("PROLED XDR") MacBook Pro costs about $400 more and does
nothing for this work. Waiting: reports say there will be no M6 Pro/Max, and M7 Pro/Max
is rumored for late 2027, so there is no reason to wait.

If you ever want 100B+ models or much faster training: Mac Studio M5 Ultra with 256 GB,
about $9,499.

Sources: support.apple.com/126319 (memory options), mac-dvd.com configurator report
(Aug 27, 2026), 9to5mac.com price-increase report (Jun 25, 2026), vdf.ai Mac Studio
pricing (Nov 4, 2026).

## 2. Before it arrives

Nothing to install. Have these ready:
1. Your GitHub login (the repo is private: github.com/jaymoore74/reit-street).
2. The name and email to send to the SEC as the user agent (the SEC requires one).
3. Optional: a free Hugging Face account. The models we use are public and download
   without one; an account only raises download limits.

## 3. Day 1: install (about 45 minutes)

Open Terminal (Applications > Utilities > Terminal) and run each block.

**3.1 Apple's command line tools** (git, compilers). A window pops up; click Install.
```
xcode-select --install
```

**3.2 Homebrew** (the Mac package manager). Paste the line from https://brew.sh, then
follow the two "Next steps" lines it prints to add brew to your PATH. Check:
```
brew --version
```

**3.3 Get the code.** GitHub will ask you to sign in the first time.
```
mkdir -p ~/code && cd ~/code
git clone https://github.com/jaymoore74/reit-street.git
cd reit-street
```

**3.4 Run the setup script.**
```
scripts/mac/setup.sh
```
What it does, so nothing is a surprise:
- installs MySQL 8.4 (LTS, supported to 2032, same major version as Aurora), Python 3.12
  and git with Homebrew, and starts MySQL
- creates the `reitstreet` database, a read-write user `reit` and a read-only user
  `reit_ro` (the model only ever gets the read-only one)
- makes up long random passwords and stores them in your macOS Keychain (service
  `reitstreet-db`), so no password sits in a file
- creates `.venv`, installs the code with the Mac extras (mlx-lm for serving and training)
- copies `config/config.local.example.ini` to `config.local.ini` and asks for the SEC
  user agent
- creates the tables, loads the 189-REIT universe and runs `reitstreet-ai doctor`

The doctor will say the model server is not running. That is expected until step 6.

From now on, in every new Terminal window:
```
cd ~/code/reit-street
source scripts/mac/common.sh
```
That turns on the local profile (`REITSTREET_PROFILE=local`), reads the passwords from the
Keychain and activates `.venv`.

## 4. First data load (overnight)

```
scripts/mac/first_load.sh
```
This pulls full SEC history for every REIT, fetches IR supplementals, parses them, builds
the property master, then runs the data-quality checks and a spot check against SEC for
PLD, O and SPG. Check the result in the morning:
```
reitstreet status
reitstreet dq
```

If you have the older supplementals on a drive, import them before training (more
history makes a better model):
```
reitstreet import-archive /path/to/folder   # PDF, XLSX or CSV; --manifest for a CSV of ticker and period
reitstreet parse-docs
```

## 5. Put the jobs on a schedule

```
scripts/mac/install_launchd.sh
```
This installs launchd agents (the Mac's built-in scheduler), in your local time:

| Job | When |
|---|---|
| SEC sync | weekdays, every 30 minutes, 5:00 AM to 6:30 PM |
| IR fetch | weekdays, 6:00 AM and 3:00 PM |
| Weekly rebuild, checks and a fresh training dataset | Sundays, 6:00 AM |
| Monitor | every hour |
| Database backup (kept 14 days) | daily, 2:30 AM |

A job due while the lid is closed runs when the Mac wakes. Logs are in `data/logs/`. To
remove them all: `scripts/mac/install_launchd.sh --remove`.

## 6. Get the model

See what fits this Mac:
```
reitstreet-ai models
```

Download and convert the primary model (about 55 GB downloaded, 29 GB on disk after
conversion, 20-40 minutes):
```
reitstreet-ai models --convert qwen3.8-27b-8bit
reitstreet-ai models --convert qwen3.8-27b-4bit
```
The 4-bit one is for the quick smoke test in step 9. We convert from the original Qwen
weights instead of downloading the ready-made mlx-community build because those were made
with a different tool (mlx-vlm) and mlx-lm training expects its own conversion.

| Model | Role | License | Why |
|---|---|---|---|
| Qwen3.8-27B (8-bit) | primary | Apache-2.0 | best instruction following of the models that fit (IFBench 79.5); a known LoRA recipe on Mac |
| Qwen3.8-27B (4-bit) | fast, smoke tests | Apache-2.0 | same model, about twice as fast |
| Qwen3.6-35B-A3B (6-bit) | bulk extraction | Apache-2.0 | only 3B active parameters, fastest for thousands of pages |
| Muse-Glimmer-30B (4-bit) | challenger | Apache-2.0 | strongest tool use; test it against the primary on Q&A |

All four are Apache-2.0: commercial use, fine-tuning and selling what they produce are
allowed with no user or revenue limits. Avoid models under the Qwen Community License or
the Llama license for this product; both carry conditions on commercial use.

## 7. Serve it and try it

Start the model server (it listens on this Mac only, 127.0.0.1:8080):
```
reitstreet-ai serve
```
Leave that window open and use a second one (with `source scripts/mac/common.sh`). Or keep
it running in the background, started at login:
```
scripts/mac/install_launchd.sh --model
```

Check everything:
```
reitstreet-ai doctor
reitstreet-ai ping
```

Ask the warehouse a question:
```
reitstreet-ai ask "Give me everything on NNN"
reitstreet-ai ask "Which apartment REITs grew same-store NOI fastest last year?"
```
The model writes SQL; only read-only queries on the client views run, as the read-only
user, with a 15-second limit. Add `--json` to see the exact queries it ran.

Read a supplemental page:
```
reitstreet-ai extract --pdf ~/Downloads/ESS_3Q26_supplemental.pdf --pages 12-14 --ticker ESS --period-end 2026-09-30
```
Every number it returns was found printed on the page; anything it made up is dropped and
counted. Scale ("in thousands") is applied by our code, not by the model.

## 8. Build the training data

```
reitstreet-ai build-dataset
```
Writes `data/ai/datasets/current/`:
- `train.jsonl`, `valid.jsonl`, `test.jsonl`: chat examples in the format mlx-lm expects
- `eval_extract.jsonl`, `eval_qa.jsonl`: the exam
- `manifest.json`: counts, settings and a hash of every file, so a run can be traced to
  its data

Where the examples come from:
- **Extraction:** supplemental pages the parser read with confidence of 0.9 or more,
  keeping only numbers that are printed on the page. The model learns to do what the
  parser does, then generalizes to layouts the parser has no rule for.
- **Q&A:** questions answered by running real SQL on the warehouse, so every answer in
  training is true. Also "not available" examples, so it learns to say so instead of
  guessing.
- **Split by company:** about 10% of REITs are held out for testing and never seen in
  training.

Gold values: copy `eval/gold.example.jsonl` to `eval/gold.jsonl` and add numbers you have
checked yourself against filings. Then:
```
reitstreet-ai build-dataset --gold eval/gold.jsonl
```
Every gold value must be answered right, every time, before a model is used.

The Sunday job builds a fresh dataset each week (`data/ai/datasets/weekly-YYYYMMDD`).

## 9. Measure the base model first

```
reitstreet-ai eval --label base
```
Saves `data/ai/datasets/current/reports/base.json` with:
- extraction recall, precision and the share of made-up numbers
- Q&A accuracy, and gold accuracy
- pass or fail on each gate

This is the number to beat. Without it you cannot tell whether training helped.

## 10. Train

**10.1 Smoke test** (about 15 minutes; proves the whole loop works):
```
reitstreet-ai train --recipe smoke
```

**10.2 The real run** (a few hours on an M5 Max; plug it in, keep the lid open or use
`caffeinate -i` in front of the command):
```
caffeinate -i reitstreet-ai train --recipe extract-qa
```
Each run gets its own folder, `data/ai/runs/<time>-extract-qa/`, with the recipe, the
generated mlx-lm config, the dataset manifest, the git commit, the full log and the
adapter. Nothing is overwritten.

The recipe (`src/reitstreet/ai/recipes/extract-qa.toml`) sets: LoRA rank 32 on 16 layers,
learning rate 1e-5, 1,000 steps, batch 1 with 4-step gradient accumulation, sequences up
to 6,144 tokens, prompt masking (it learns the answers, not the pages). To change any of
it, copy the recipe to a new name and edit the copy, so old runs stay explainable.

To see the command without running it: add `--dry-run`.

If it runs out of memory: lower `max_seq_length` to 4096 or `num_layers` to 8 in your
copy of the recipe.

## 11. Is it better? Prove it

Point the server at the new run and score it on the same exam:
```
reitstreet-ai serve --run data/ai/runs/<time>-extract-qa --adapter
reitstreet-ai eval --label run1 --baseline data/ai/datasets/current/reports/base.json
```
The report shows each number next to the base model's. Gates to pass before use:

| Gate | Threshold |
|---|---|
| Extraction recall | 95% or more |
| Extraction precision | 97% or more |
| Made-up numbers | 0.5% or less |
| Q&A accuracy | 90% or more |
| Gold values | 100% |

If it fails a gate, do not use it. Look at the misses in the report first: most often the
fix is better training data (more history, more gold values), not more training.

## 12. Use the trained model

Merge the adapter into the model so it loads as one:
```
reitstreet-ai fuse --run data/ai/runs/<time>-extract-qa
```
Then make it the model the background server runs, and restart the server:
```
echo data/ai/runs/<time>-extract-qa > data/ai/SERVING
launchctl kickstart -k gui/$(id -u)/com.reitstreet.model
```
To go back to the base model: `rm data/ai/SERVING` and run the same `launchctl` line.

## 13. Keep it current

Once a month (or after a big archive import):
1. `reitstreet-ai build-dataset --gold eval/gold.jsonl`
2. `reitstreet-ai eval --label base-YYYYMM` (base model, new exam)
3. `caffeinate -i reitstreet-ai train --recipe extract-qa`
4. `reitstreet-ai eval --label run-YYYYMM --baseline <last month's report>`
5. Switch only if every gate passes and it beats last month.

## 14. Switching between the Mac and Aurora

The same code runs both. The profile picks the config file:
- `REITSTREET_PROFILE=local` reads `config.local.ini` (MySQL on the Mac). `common.sh`
  sets this for you.
- No profile reads `config.ini` (Aurora, as in docs/runbooks/cloud-setup.md).

Moving data from Aurora to the Mac later is a `mysqldump` from the AWS runner and a
restore on the Mac; that runbook will come when Aurora is live.

## 15. If something goes wrong

| Symptom | Fix |
|---|---|
| `doctor` says database NO | `brew services restart mysql@8.4`, then `source scripts/mac/common.sh` again |
| `doctor` says model server NO | start `reitstreet-ai serve`, or check `data/logs/launchd-model.log` |
| `... is missing: run reitstreet-ai models --convert ...` | run that command |
| Training is very slow or crashes on Qwen3.8 | update mlx-lm (`pip install -U "mlx-lm[train]"`); the recipes already set `MLX_DISABLE_COMPILE=1`, which a published Qwen3.8 run needed |
| Out of memory while training | in a copy of the recipe: `max_seq_length = 4096`, then `num_layers = 8` |
| A job did not run | `launchctl list | grep reitstreet`, and the log in `data/logs/` |
| You want JSON enforced while decoding | install Ollama (`brew install ollama`), set `[llm] backend = ollama` and the model name in `config.local.ini`; Ollama enforces a JSON schema, mlx_lm.server does not |

## Reference

- Commands: `reitstreet-ai --help` and `reitstreet-ai <command> --help`
- Design and reasons: docs/adr/0012-local-model.md
- Model list and sizes: src/reitstreet/ai/models.py
- Training recipes: src/reitstreet/ai/recipes/
