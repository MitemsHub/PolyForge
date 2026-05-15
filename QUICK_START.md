# Quick Start (One Page)

## Local Development (Python)

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m pytest tests/ -q --tb=no
python -m src.main --full-check
python -m src.main --scan-only
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m pytest tests/ -q --tb=no
python -m src.main --full-check
python -m src.main --scan-only
```

## Dashboard Only

Local:

```bash
python -m src.main --dashboard
```

Docker:

```bash
docker compose up -d --build
```

## Full Production (Docker)

Run scheduler + dashboard together:

```bash
docker compose up -d --build
docker compose ps
docker compose exec polyforge python -m src.main --healthcheck
docker compose exec polyforge python -m src.main --full-check
```

Optional infra (Postgres + Redis):

```bash
docker compose --profile infra up -d --build
```

Stop:

```bash
docker compose down
```
