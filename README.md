# lemon_home

Agent-friendly apartment management CLI backed by local files.

## Goals

- Manage whole apartments as units
- Track leases with multiple tenants and one primary tenant
- Preserve tenant and lease history
- Store media files inside the system data directory
- Support monthly bills, partial payments, and deposit refunds
- Expose a stable CLI that works well for humans and coding agents

## Tech Stack

- Python 3.12+
- `typer` for CLI commands
- Local JSON files plus media folders

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Default data directory: `./data`

You can override it with:

```bash
export LEMON_HOME_DATA_DIR=/path/to/data
```

## Example

```bash
lemon-home house add \
  --address "Shanghai Pudong XX Road 100" \
  --area 88.5 \
  --monthly-rent 7200 \
  --deposit 7200 \
  --layout "2B1B"

lemon-home lease create \
  --house-id H0001 \
  --start-date 2026-03-01 \
  --billing-day 1

lemon-home tenant add \
  --house-id H0001 \
  --lease-id L0001 \
  --name "Zhang San" \
  --id-number "310101199001010011" \
  --phone "13800138000" \
  --id-front ./samples/id-front.jpg \
  --id-back ./samples/id-back.jpg \
  --primary

lemon-home bill generate \
  --house-id H0001 \
  --lease-id L0001 \
  --month 2026-03

lemon-home payment add \
  --house-id H0001 \
  --lease-id L0001 \
  --bill-id 2026-03 \
  --amount 3000
```

## Web GUI

Start the local web server:

```bash
source .venv/bin/activate
lemon-home-web
```

Or use the module entrypoint:

```bash
python -m lemon_home.web
```

Convenience scripts:

```bash
./start_web.sh
```

```powershell
.\start_web.ps1
```

Default address:

```text
http://127.0.0.1:8000
```

For phone access on the same network, keep the default host `0.0.0.0` and open:

```text
http://<your-lan-ip>:8000
```

Optional overrides:

```bash
export LEMON_HOME_HOST=0.0.0.0
export LEMON_HOME_PORT=8000
```

Current Web GUI supports:

- Dashboard with house and receivable stats
- Dashboard filters by house status and debt state
- Cross-house bill center with month and status filters
- Add house
- House detail view
- Upload house photos and videos
- Create lease
- Add tenants with ID front/back photos
- Generate bills
- Record partial payments
- Record deposit refund
- Checkout lease

CLI and Web GUI share the same `data/` directory.

## Layout

```text
data/
  houses/
    H0001/
      house.json
      media/
        photos/
        videos/
      history.jsonl
      leases/
        L0001/
          lease.json
          deposit.json
          tenants/
            T0001/
              tenant.json
              id_front.jpg
              id_back.jpg
          bills/
            2026-03.json
          payments/
            P0001.json
          history.jsonl
```

## Core Commands

- `lemon-home house add|list|show|update|archive`
- `lemon-home lease create|show|checkout`
- `lemon-home tenant add|list`
- `lemon-home bill generate|list`
- `lemon-home payment add|list`
- `lemon-home deposit refund`
- `lemon-home history show`

Every read command supports `--json`. Sensitive fields are masked by default; use `--full` to show raw values.
