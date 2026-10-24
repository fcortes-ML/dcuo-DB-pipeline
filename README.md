# dcuo-DB-pipeline

A data engineering project that builds a historical database of DC Universe Online character progression (feats, skill points and stats) from Daybreak Games' public Census API.

## The problem

The Census API reports which feats a character has completed, but only as numeric IDs. There is no collection with feat names, descriptions or point values. A community catalog of feat definitions exists, but it has no IDs and stopped being updated in 2023.

Joining the two requires building a bridge table from evidence: diffs between snapshots of the same character, set comparisons between characters with different attributes, and checksums based on feat point totals. This project builds the pipeline that makes that possible, one stage at a time.

## Status

| Stage | Description | Status |
|---|---|---|
| 1 | Project setup and raw extraction | Done |
| 2 | API exploration and hypothesis testing | Next |
| 3 | Feat catalog extraction and quality checks | Planned |
| 4 | Cloud load: GCS landing, BigQuery raw tables and dbt staging models | Planned |
| 5 | Bridge table: mapping feat IDs to feat definitions (built as tested dbt models) | Planned |
| 6 | Automation: scheduled runs and orchestration | Planned |

## How it works

`extract.py`:

1. Reads the world and the list of characters from `config.toml`.
2. Resolves each `character_id` by name and world.
3. Fetches three collections per character: `character`, `characters_completed_feat` and `characters_active_feat`.
4. Stores every API response untouched, partitioned by collection and UTC date (the same layout planned for the GCS landing zone).
5. Validates each response (API errors, row counts, truncation and pagination, duplicate IDs, rows belonging to another character) and writes a run manifest.

A character that fails does not stop the run: it is flagged in the manifest and the script exits with code 1.

### Output layout

```
data/
├── raw/
│   ├── character/dt=YYYY-MM-DD/<run_id>__<character_id>__p0.json
│   ├── characters_completed_feat/dt=YYYY-MM-DD/...
│   └── characters_active_feat/dt=YYYY-MM-DD/...
└── runs/<run_id>.json        # run manifest
```

`data/` is excluded from version control.

## Setup

Requirements: Python 3.11+ and a Census service ID (free registration at [census.daybreakgames.com](https://census.daybreakgames.com)).

```powershell
git clone https://github.com/fcortes-ML/dcuo-DB-pipeline.git
cd dcuo-DB-pipeline
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Configuration:

1. Copy `config.example.toml` to `config.toml` and list your characters. `config.toml` is gitignored.
2. Store your service ID in the `DCUO_SERVICE_ID` environment variable, then open a new terminal:

   ```powershell
   [Environment]::SetEnvironmentVariable("DCUO_SERVICE_ID", "your_service_id", "User")
   ```

   On macOS or Linux: `export DCUO_SERVICE_ID=your_service_id`

Run:

```powershell
.\.venv\Scripts\python.exe extract.py
```

## Design decisions

- **Raw data is stored untouched and append-only**, so every transformation can be rebuilt without calling the API again.
- **Date partitions use UTC**, matching GCS and BigQuery conventions.
- **No credentials or personal configuration in the repository**: the service ID comes from an environment variable and the character list from a gitignored file.
- **API-friendly**: a minimum interval between requests and retries with exponential backoff.

## Tech stack

Current: Python (`requests`) and the Census REST API.
Planned: Google Cloud Storage, BigQuery, dbt (staging models, tests and docs) and a workflow orchestrator.

## Planned modeling with dbt

Once the raw snapshots are loaded into BigQuery, transformations will be managed with dbt:

- **Staging**: one model per collection, flattening the JSON and keeping the latest load per date.
- **Marts**: characters, feat catalog and completed feats per character.
- **Tests**: unique and not-null IDs, relationships between feats and the catalog, accepted values for decoded codes, and a feat point-total checksum.
- **Seeds**: small lookup tables for decoded IDs.

## Data source

Data comes from Daybreak Game Company's public Census API, used with a registered service ID. This is a personal, non-commercial project and is not affiliated with or endorsed by Daybreak Game Company. DC Universe Online and related names are trademarks of their respective owners.
