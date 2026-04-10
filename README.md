# Volunteer Cloud

A distributed computing platform that leverages volunteer worker nodes to execute computational tasks. Built with Flask for the control server and Python for worker agents, featuring a secure plugin system for extensible task execution.

## Project Structure

```
volunteer_cloud/
├── control_server/
│   ├── server.py              # Main Flask application handling worker management, task scheduling, and API endpoints
│   ├── keys/                  # Ed25519 signing keys for plugin authentication
│   │   ├── admin_private_key.pem
│   │   └── admin_public_key.pem
│   ├── storage/               # CSV persistence for logs and worker data
│   │   ├── worker_logs.csv    # Task execution history
│   │   ├── session_logs.csv   # Worker session tracking
│   │   └── worker_info.csv    # Persistent worker metadata
│   └── templates/             # Jinja2 HTML templates for the web dashboard
│       ├── index.html         # Main dashboard with real-time stats
│       ├── analysis.html      # Analytics and performance charts
│       └── worker.html        # Individual worker detail pages
├── plugins/                   # Plugin bundles for different task types
│   ├── prime_number/          # Example plugin for prime number computation
│   │   ├── plugin.py          # Task execution script
│   │   ├── manifest.json      # Plugin metadata and SHA256 hash
│   │   └── manifest.sig       # Ed25519 signature for authenticity
│   ├── matrix_multiplication/ # Matrix multiplication plugin
│   ├── hash_workload/         # Cryptographic hash workload plugin
│   ├── sort_arrays/           # Array sorting plugin
│   └── monte_carlo_pi/        # Monte Carlo Pi estimation plugin
└── worker/
    ├── worker.py              # Worker agent that downloads and executes plugins
    ├── plugin_cache/          # Local cache of downloaded plugin bundles
    └── job_runs/              # Temporary directories for task execution
```

## Architecture and Logic

### Core Components

1. **Control Server (`server.py`)**:
   - **Flask Web App**: Serves the dashboard UI and provides REST APIs for worker communication.
   - **Worker Management**: Registers workers, tracks their status (online/busy/offline), and stores metadata in CSV files.
   - **Task Scheduling**: Uses a benchmark-aware algorithm to assign tasks to the most suitable workers.
   - **Plugin Store**: Loads and serves signed plugin bundles from the `plugins/` directory.
   - **Fault Tolerance**: Monitors worker heartbeats and requeues tasks if workers disconnect.

2. **Worker Agent (`worker.py`)**:
   - **Registration**: Connects to the server, sends hardware specs and benchmark score.
   - **Heartbeat Loop**: Sends periodic pings to prove liveness.
   - **Task Execution**: Downloads plugins, verifies signatures and hashes, then executes tasks in isolated environments.
   - **Progress Reporting**: Parses plugin output for progress updates and sends them to the server.

3. **Plugin System**:
   - **Security**: Uses Ed25519 digital signatures to ensure plugin authenticity and integrity.
   - **Execution**: Plugins are Python scripts that run in subprocesses with timeout and resource limits.
   - **Communication**: Plugins emit progress via stdout and write results to JSON files.

### Key Logic Flows

#### Worker Registration and Heartbeat
- Worker benchmarks its CPU performance using SHA256 hashing.
- Sends specs to `/api/register_worker` and receives a session ID.
- Starts heartbeat thread sending updates to `/api/heartbeat` every 2 seconds.
- Server marks workers offline after 6 seconds of missed heartbeats.

#### Task Assignment
- Server generates tasks via `/api/generate_tasks` (currently random selection of plugin types).
- Workers poll `/api/request_task` every second.
- Scheduler assigns tasks based on worker benchmark scores:
  - High-score workers get hardest tasks (e.g., matrix multiplication).
  - Low-score workers get easier tasks (e.g., prime counting).
- Task payload includes plugin URLs, manifest, and signature.

#### Plugin Execution
- Worker downloads plugin bundle if not cached.
- Verifies manifest signature against hardcoded public key.
- Checks plugin SHA256 hash against manifest.
- Executes plugin with difficulty parameter and output path.
- Monitors stdout for progress updates and enforces timeouts.
- Reports completion or failure back to server.

#### Persistence and Logging
- Worker metadata saved to `worker_info.csv` for restart recovery.
- Task results logged to `worker_logs.csv` with timestamps and points.
- Session data tracked in `session_logs.csv` for uptime analysis.

## Key Features

- **Distributed Execution**: Leverages multiple volunteer machines for parallel computing.
- **Smart Scheduling**: Benchmark-based task assignment optimizes resource utilization.
- **Secure Plugins**: Cryptographic signing prevents malicious plugin execution.
- **Real-time Monitoring**: Live dashboard with charts and worker status updates.
- **Fault Tolerance**: Automatic task requeueing on worker failures.
- **Persistence**: CSV-based storage survives server restarts.
- **Extensible**: Easy to add new task types via the plugin system.

## Requirements

- Python 3.8+ (tested with 3.12)
- Dependencies:
  - `flask` - Web framework
  - `requests` - HTTP client
  - `psutil` - System information
  - `colorama` - Terminal colors
  - `cryptography` - Ed25519 signing

## Installation and Setup

1. **Clone or Download** the project to your local machine.

2. **Install Dependencies**:
   ```bash
   pip install flask requests psutil colorama cryptography
   ```

3. **Generate Keys** (if needed):
   The project includes pre-generated Ed25519 keys. To regenerate:
   ```python
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   from cryptography.hazmat.primitives import serialization
   priv = Ed25519PrivateKey.generate()
   pub = priv.public_key()
   # Save to control_server/keys/
   ```

4. **Configure Network** (optional):
   - Edit `worker/worker.py` `SERVER_URL` or set environment variable `VOLUNTEER_SERVER_URL`.
   - Default: `http://192.168.1.48:3000` (change to your server's IP).

## Running the Project

### Start the Control Server

```bash
cd volunteer_cloud/control_server
python server.py
```

- Server starts on `http://0.0.0.0:3000`.
- Access dashboard at `http://localhost:3000`.

### Start Worker Nodes

On each volunteer machine:

```bash
cd volunteer_cloud/worker
python worker.py
```

- Workers will benchmark themselves, register with the server, and start processing tasks.
- Multiple workers can run simultaneously for distributed computing.

### Generate Tasks

- Use the "Generate Plugin Workload" button on the dashboard.
- Or call the API: `POST http://localhost:3000/api/generate_tasks`.

## UI Pages

- **Dashboard** (`/`): Real-time overview with worker stats, task charts, and plugin list.
- **Analysis** (`/analysis`): Historical analytics and performance metrics.
- **Worker Details** (`/worker/<worker_id>`): Individual worker history and stats.

## Plugin System

Plugins enable secure, extensible task execution:

### Creating a Plugin

1. **Write Plugin Script** (`plugins/my_task/plugin.py`):
   ```python
   import argparse
   import json

   def emit_progress(pct):
       print(f"PROGRESS:{pct}", flush=True)

   def main():
       parser = argparse.ArgumentParser()
       parser.add_argument("--difficulty", type=int, required=True)
       parser.add_argument("--output", type=str, required=True)
       args = parser.parse_args()

       # Your computation logic here
       result = compute_something(args.difficulty)
       emit_progress(100)

       with open(args.output, "w") as f:
           json.dump({"task_type": "my_task", "result": result}, f)

   if __name__ == "__main__":
       main()
   ```

2. **Server Auto-Generates Manifest and Signature** on startup.

3. **Add to Task Generation** in `server.py` `generate_tasks()`.

### Security Model

- **Authenticity**: Ed25519 signatures verify plugins come from the admin.
- **Integrity**: SHA256 hashes prevent tampering.
- **Isolation**: Plugins run in subprocesses with timeouts.
- **Authorization**: Workers only execute assigned, verified plugins.

## Customization

- **Add Task Types**: Create new plugins in `plugins/` and update `generate_tasks()`.
- **Modify Scheduling**: Edit `get_best_task_for_worker()` in `server.py`.
- **Customize UI**: Update templates in `control_server/templates/`.
- **Change Persistence**: Replace CSV logic with a database if needed.

## Notes

- Workers poll for tasks every second and send heartbeats every 2 seconds.
- Plugin execution is sandboxed via subprocess timeouts (default 120s).
- Server persists worker data across restarts using CSV files.
- For production, consider HTTPS, authentication, and database storage.
- The system is designed for CPU-bound tasks; memory-intensive workloads may need adjustments.

## Troubleshooting

- **Workers not connecting**: Check `SERVER_URL` and firewall settings.
- **Plugin verification fails**: Ensure keys are correctly placed and plugins are signed.
- **Tasks not executing**: Verify plugin scripts have correct output format.
- **Performance issues**: Monitor worker benchmarks and adjust task difficulties.
