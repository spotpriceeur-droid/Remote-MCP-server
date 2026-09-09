# energi-dk MCP Server

An MCP server that exposes your Energi Data Service / TimescaleDB pipeline
as tools an MCP client (Claude Desktop, Claude Code, etc.) can call
directly — e.g. "how many negative price hours has DK1 had this year and
which hours of the day do they cluster in?"

It reuses your existing pipeline code as-is (`api/`, `storage/`,
`processing/`, `config.py`, `schema.py`, `exceptions.py`) — `server.py` is
the only new file, wiring those pieces up as MCP tools.

## What it does differently from `main.py`

`main.py` is a batch job: fetch everything, write it all to TimescaleDB.
This server is read-oriented and built for on-demand questions:

- **TimescaleDB is optional at query time.** If it's reachable, tools read
  from it (fast). If it's not running, every tool falls back to fetching
  directly from Energi Data Service instead — you still get an answer.
- **Always includes the most recent data.** Even when a tool serves most
  of a date range from TimescaleDB, it tops up the tail with a fresh,
  direct API call up to "now" (or your requested end time), so results
  reflect prices published since you last ran the pipeline — no need to
  re-run `main.py` before asking a question.
- **Negative-price counting uses raw observations**, not interpolated
  ones (hourly Elspotprices before 2025-10-01, native 15-min
  DayAheadPrices from 2025-10-01 onward) — interpolation is great for a
  continuous series but would distort a negative-price count.

## Tools

| Tool | What it's for |
|---|---|
| `negative_price_summary(zone, start, end)` | Count of negative-price observations + breakdown by hour of day |
| `fetch_latest_prices(zone, hours)` | Most recent prices, straight from the API, no DB involved |
| `query_prices(zone, start, end)` | Min/max/mean + sample records for a range |
| `refresh_database()` | Runs the full pipeline into TimescaleDB (same as `main.py`) |

## 1. Install dependencies

With [uv](https://docs.astral.sh/uv/) (recommended, matches this project's layout):

```bash
cd energi-dk
uv sync
```

Or with plain pip:

```bash
cd energi-dk
pip install -e .
```

## 2. (Optional) Start TimescaleDB

Only needed if you want fast historical queries backed by a database
instead of live API calls every time. Skip this if you just want quick
answers — the server works without it.

```bash
docker run -d --name timescaledb -p 5432:5432 -e POSTGRES_PASSWORD=yourpassword \
  -v timescale_data:/var/lib/postgresql/data timescale/timescaledb:latest-pg16
```

Set the password to match, either in `config.py` (`DatabaseConfig.password`)
or via the `TIMESCALE_PASSWORD` environment variable.

## 3. Connect it to your MCP client

### Claude Desktop / Claude Code

Add to your MCP config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "energi-dk": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/energi-dk", "run", "server.py"]
    }
  }
}
```

(Using plain Python instead of uv? Use `"command": "python", "args": ["/absolute/path/to/energi-dk/server.py"]` instead, after `pip install -e .` in that environment.)

Restart the client, and the tools above will be available in conversation.

## 4. Try it

Once connected, just ask in chat:

> "How many negative price hours has DK1 had in 2026, and which hours of
> the day do they cluster in?"

The assistant will call `negative_price_summary("DK1", "2026-01-01", "now")`.

## Zones

`DK1`, `DK2`, `SE1`, `SE2`, `SE3`, `SE4`, `NO1`, `NO2`, `NO3`, `NO4`, `NO5`, `FI`

## Notes

- `start`/`end` accept plain dates (`"2026-01-01"`) or date-times
  (`"2026-01-01T00:00"`); `end` also accepts `"now"`.
- `refresh_database()` requires TimescaleDB to be running; the other three
  tools don't.
