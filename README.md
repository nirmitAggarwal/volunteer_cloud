# Volunteer Cloud

A simple volunteer distributed compute platform with a Flask control server and Python worker nodes.

## Project Structure

- `control_server/`
  - `server.py` - Flask controller that manages worker registration, task scheduling, progress tracking, and persistence.
  - `templates/` - Dashboard UI templates for live monitoring and analysis.
    - `index.html` - Real-time controller dashboard.
    - `worker.html` - Worker detail page.
    - `analysis.html` - Fleet analytics page.
  - `storage/` - CSV persistence files.
    - `worker_logs.csv` - Task execution history.
    - `session_logs.csv` - Worker session history.
    - `worker_info.csv` - Persisted worker metadata and scores.

- `worker/`
  - `worker.py` - Worker agent that registers with the server, fetches tasks, reports progress, and returns task results.

## What it does

- Registers volunteer worker nodes and keeps them online with heartbeat checks
- Assigns queued tasks using a simple benchmark-aware scheduler
- Tracks task progress, completion, retries, and points
- Persists worker metadata so the dashboard can show known devices after restart
- Provides a UI dashboard with charts, live worker fleet view, and task queue
- Provides an analysis page for worker performance and task health

## Key Features

- Smart task assignment based on worker benchmark score
- Graceful handling of worker disconnects and task requeueing
- CSV logging for both worker sessions and task activity
- Persistent worker information across server restarts
- Visual dashboards using Tailwind and Chart.js

## Requirements

- Python 3.12+ (or compatible Python 3 version)
- `Flask`
- `requests`
- `psutil`
- `colorama`

Install dependencies with:

```bash
pip install flask requests psutil colorama
```

## Running the Project

### Start the controller

```bash
cd control_server
python server.py
```

The server runs on `http://127.0.0.1:3000` by default.

### Start a worker

```bash
cd worker
python worker.py
```

If the worker is running on a different machine from the server, set the server URL first:

```bash
set VOLUNTEER_SERVER_URL=http://<server-ip>:3000
python worker.py
```

## UI Pages

- Dashboard: `http://127.0.0.1:3000/`
- Analysis: `http://127.0.0.1:3000/analysis`
- Worker details: `http://127.0.0.1:3000/worker/<worker_id>`

## Notes

- The worker sends heartbeats every 2 seconds and requests new tasks every second.
- If the server restarts, the last-known worker metadata is loaded from `storage/worker_info.csv`.
- Task logs and session history are written as CSV for later review.

## Customization

- Add new task types in `worker/worker.py` and define matching task generation or queueing logic in `control_server/server.py`.
- Modify the UI templates in `control_server/templates/` for custom dashboard views.
