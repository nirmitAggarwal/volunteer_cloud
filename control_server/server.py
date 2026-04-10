import os
import csv
import time
import uuid
import json
import logging
import threading
import hashlib
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from colorama import init, Fore, Style

# Initialize terminal colors
init(autoreset=True)

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.disabled = True # Disable default flask logs for custom clean logs

BASE_DIR = Path(__file__).resolve().parent.parent
CONTROL_DIR = Path(__file__).resolve().parent
STORAGE_DIR = CONTROL_DIR / 'storage'
WORKER_LOGS = STORAGE_DIR / 'worker_logs.csv'
SESSION_LOGS = STORAGE_DIR / 'session_logs.csv'
WORKER_INFO = STORAGE_DIR / 'worker_info.csv'
PLUGIN_DIR = BASE_DIR / 'plugins'
KEY_DIR = CONTROL_DIR / 'keys'
PRIVATE_KEY_PATH = KEY_DIR / 'admin_private_key.pem'
PUBLIC_KEY_PATH = KEY_DIR / 'admin_public_key.pem'
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
KEY_DIR.mkdir(parents=True, exist_ok=True)

# In-Memory State
state_lock = threading.RLock()
workers = {} # worker_id -> dict
tasks = {}   # task_id -> dict
sessions = {} # session_id -> dict
plugins = {} # plugin_id -> metadata

# --- CSV Initialization ---
def init_csv(filepath, headers):
    if not os.path.exists(filepath):
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

init_csv(WORKER_LOGS, ['timestamp', 'worker_id', 'session_id', 'task_id', 'task_type', 'benchmark_score', 'start_time', 'end_time', 'time_taken', 'points_earned', 'status', 'progress_last_seen', 'error_message'])
init_csv(SESSION_LOGS, ['worker_id', 'session_id', 'connected_at', 'disconnected_at', 'uptime_seconds', 'total_tasks_done', 'total_points'])
init_csv(WORKER_INFO, ['worker_id', 'hostname', 'os', 'cpu', 'cores', 'ram', 'benchmark_score', 'ip', 'last_seen', 'total_points', 'total_tasks', 'known_since'])


def load_csv_rows(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', newline='') as f:
        reader = csv.DictReader(f)
        return [row for row in reader]
def save_worker_info():
    headers = ['worker_id', 'hostname', 'os', 'cpu', 'cores', 'ram', 'benchmark_score', 'ip', 'last_seen', 'total_points', 'total_tasks', 'known_since']
    rows = []
    with state_lock:
        for worker in workers.values():
            rows.append({
                'worker_id': worker['worker_id'],
                'hostname': worker['hostname'],
                'os': worker['os'],
                'cpu': worker['cpu'],
                'cores': worker['cores'],
                'ram': worker['ram'],
                'benchmark_score': worker['benchmark_score'],
                'ip': worker.get('ip', ''),
                'last_seen': worker.get('last_heartbeat', 0),
                'total_points': worker.get('total_points', 0),
                'total_tasks': worker.get('total_tasks', 0),
                'known_since': worker.get('known_since', 0)
            })
    with open(WORKER_INFO, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
def load_known_workers():
    for row in load_csv_rows(WORKER_INFO):
        try:
            cores = int(row.get('cores', 0))
            bench = int(row.get('benchmark_score', 0))
            points = float(row.get('total_points', 0))
            total_tasks = int(row.get('total_tasks', 0))
            known_since = float(row.get('known_since', 0))
        except ValueError:
            continue
        workers[row['worker_id']] = {
            'worker_id': row['worker_id'],
            'hostname': row['hostname'],
            'os': row['os'],
            'cpu': row['cpu'],
            'cores': cores,
            'ram': row['ram'],
            'benchmark_score': bench,
            'ip': row.get('ip', ''),
            'status': 'offline',
            'last_heartbeat': 0,
            'current_task': None,
            'current_session': None,
            'total_points': points,
            'total_tasks': total_tasks,
            'known_since': known_since
        }
load_known_workers()


def compute_sha256(filepath):
    with open(filepath, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def sign_manifest(private_key, manifest_data):
    encoded = json.dumps(manifest_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return private_key.sign(encoded)

def load_private_key():
    with open(PRIVATE_KEY_PATH, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def load_public_key():
    with open(PUBLIC_KEY_PATH, 'rb') as f:
        return serialization.load_pem_public_key(f.read())

def ensure_plugin_bundles():
    private_key = load_private_key()
    plugin_definitions = [
        ('prime_number', 'cpu-heavy prime count plugin'),
        ('matrix_multiplication', 'matrix multiplication workload plugin'),
        ('hash_workload', 'cryptographic hash chain workload plugin'),
        ('sort_arrays', 'sort large random arrays plugin'),
        ('monte_carlo_pi', 'monte carlo pi estimation plugin')
    ]

    for plugin_id, description in plugin_definitions:
        plugin_dir = Path(PLUGIN_DIR) / plugin_id
        plugin_path = plugin_dir / 'plugin.py'
        manifest_path = plugin_dir / 'manifest.json'
        sig_path = plugin_dir / 'manifest.sig'

        if not plugin_dir.exists():
            continue
        if not plugin_path.exists():
            continue

        manifest = {
            'plugin_id': plugin_id,
            'version': 1,
            'entrypoint': 'plugin.py',
            'task_type': plugin_id,
            'description': description,
            'platform': 'python',
            'timeout_sec': 120,
            'sha256': compute_sha256(plugin_path),
            'created_at': int(time.time()),
            'args': ['--difficulty', '--output']
        }

        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)

        signature = sign_manifest(private_key, manifest)
        with open(sig_path, 'wb') as f:
            f.write(signature)

def load_plugin_store():
    for plugin_folder in Path(PLUGIN_DIR).iterdir():
        if not plugin_folder.is_dir():
            continue
        manifest_path = plugin_folder / 'manifest.json'
        if not manifest_path.exists():
            continue
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)

        plugins[manifest['plugin_id']] = {
            'plugin_id': manifest['plugin_id'],
            'entrypoint': manifest['entrypoint'],
            'plugin_url': f"/download/plugins/{manifest['plugin_id']}/{manifest['entrypoint']}",
            'manifest_url': f"/download/plugins/{manifest['plugin_id']}/manifest.json",
            'signature_url': f"/download/plugins/{manifest['plugin_id']}/manifest.sig",
            'timeout_sec': manifest.get('timeout_sec', 120),
            'sha256': manifest.get('sha256'),
            'task_type': manifest.get('task_type', manifest['plugin_id'])
        }

ensure_plugin_bundles()
load_plugin_store()

def log_print(msg, level="INFO"):
    colors = {"INFO": Fore.CYAN, "SUCCESS": Fore.GREEN, "WARN": Fore.YELLOW, "ERROR": Fore.RED, "TASK": Fore.MAGENTA}
    color = colors.get(level, Fore.WHITE)
    print(f"{Fore.LIGHTBLACK_EX}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {color}{msg}")

# --- Background Fault Tolerance Thread ---
def fault_tolerance_loop():
    while True:
        time.sleep(2)
        current_time = time.time()
        updated = False
        with state_lock:
            for wid, w in list(workers.items()):
                if w['status'] == 'offline':
                    continue
                
                # Check Heartbeat
                if current_time - w['last_heartbeat'] > 6:
                    log_print(f"Worker {w['hostname']} ({wid}) missed heartbeat. Marking offline.", "ERROR")
                    w['status'] = 'offline'
                    updated = True
                    
                    # Log Session
                    session = sessions.get(w['current_session'])
                    if session:
                        uptime = current_time - session['connected_at']
                        with open(SESSION_LOGS, 'a', newline='') as f:
                            csv.writer(f).writerow([wid, w['current_session'], session['connected_at'], current_time, uptime, session['tasks_done'], session['points']])
                    
                    # Requeue Task if busy
                    if w['current_task']:
                        tid = w['current_task']
                        task = tasks.get(tid)
                        if task and task['status'] == 'running':
                            task['status'] = 'queued'
                            task['retries_count'] += 1
                            task['assigned_worker_id'] = None
                            log_print(f"Task {tid} requeued (Worker failed). Progress was {task['progress']}%", "WARN")
                    
                    w['current_task'] = None
        if updated:
            save_worker_info()

threading.Thread(target=fault_tolerance_loop, daemon=True).start()

# --- Smart Scheduling Algorithm ---
def get_best_task_for_worker(worker):
    pending_tasks = [t for t in tasks.values() if t['status'] == 'queued']
    if not pending_tasks: return None
    
    online_benchmarks = [w['benchmark_score'] for w in workers.values() if w['status'] != 'offline']
    median_bench = sorted(online_benchmarks)[len(online_benchmarks)//2] if online_benchmarks else 0
    
    pending_tasks.sort(key=lambda x: x['estimated_compute_cost'], reverse=True)
    
    if worker['benchmark_score'] >= median_bench:
        return pending_tasks[0] # Give hardest task to strong worker
    else:
        return pending_tasks[-1] # Give easiest task to weak worker

# --- API Endpoints ---
@app.route('/download/plugins/<plugin_id>/<path:filename>')
def download_plugin_file(plugin_id, filename):
    plugin_dir = PLUGIN_DIR / plugin_id
    return send_from_directory(str(plugin_dir), filename, as_attachment=False)

@app.route('/api/public_key')
def public_key():
    with open(PUBLIC_KEY_PATH, 'rb') as f:
        return f.read(), 200, {'Content-Type': 'application/octet-stream'}

@app.route('/api/register_worker', methods=['POST'])
def register_worker():
    data = request.json
    wid = data['worker_id']
    session_id = str(uuid.uuid4())
    
    with state_lock:
        if wid not in workers:
            workers[wid] = {
                "worker_id": wid,
                "total_points": 0,
                "total_tasks": 0,
                "known_since": time.time()
            }
        
        workers[wid].update({
            "worker_id": wid,
            "hostname": data['hostname'],
            "os": data['os'],
            "cpu": data['cpu'],
            "cores": data['cores'],
            "ram": data['ram'],
            "benchmark_score": data['benchmark_score'],
            "ip": request.remote_addr,
            "status": "online",
            "last_heartbeat": time.time(),
            "current_task": None,
            "current_session": session_id
        })
        
        sessions[session_id] = {"connected_at": time.time(), "tasks_done": 0, "points": 0}
        save_worker_info()
    
    log_print(f"Worker Connected: {data['hostname']} (Bench: {data['benchmark_score']})", "SUCCESS")
    return jsonify({"status": "registered", "session_id": session_id})

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    wid = request.json.get('worker_id')
    with state_lock:
        if wid in workers and workers[wid]['status'] != 'offline':
            workers[wid]['last_heartbeat'] = time.time()
            return jsonify({"status": "ok"})
    return jsonify({"error": "Worker not registered or offline"}), 404

@app.route('/api/request_task', methods=['POST'])
def request_task():
    wid = request.json.get('worker_id')
    with state_lock:
        if wid not in workers or workers[wid]['status'] == 'offline': return jsonify({"task": None})
        
        task = get_best_task_for_worker(workers[wid])
        if task:
            task['status'] = 'running'
            task['assigned_worker_id'] = wid
            task['assigned_time'] = time.time()
            workers[wid]['current_task'] = task['task_id']
            workers[wid]['status'] = 'busy'
            log_print(f"Assigned {task['task_type']} to {workers[wid]['hostname']}", "TASK")
            plugin_meta = plugins.get(task.get('plugin_id') or task.get('task_type')) or {}
            task_payload = task.copy()
            task_payload.update({
                'plugin_id': plugin_meta.get('plugin_id'),
                'plugin_url': plugin_meta.get('plugin_url'),
                'manifest_url': plugin_meta.get('manifest_url'),
                'signature_url': plugin_meta.get('signature_url'),
                'entrypoint': plugin_meta.get('entrypoint'),
                'timeout_sec': plugin_meta.get('timeout_sec', 120)
            })
            return jsonify({"task": task_payload})
    return jsonify({"task": None})

@app.route('/api/progress_update', methods=['POST'])
def progress_update():
    data = request.json
    with state_lock:
        task = tasks.get(data['task_id'])
        if task:
            task['progress'] = data['progress']
    return jsonify({"status": "ok"})

@app.route('/api/task_result', methods=['POST'])
def task_result():
    data = request.json
    wid = data['worker_id']
    tid = data['task_id']
    success = data['success']
    time_taken = data['time_taken']
    
    with state_lock:
        task = tasks.get(tid)
        worker = workers.get(wid)
        
        if not task or not worker: return jsonify({"error": "Invalid state"})
        
        pts = (worker['benchmark_score'] * time_taken / 1000) if success else 0
        task.update({
            "status": "completed" if success else "failed",
            "progress": 100 if success else task['progress'],
            "completed_time": time.time()
        })
        worker.update({"current_task": None, "status": "online"})
        
        if success:
            worker['total_points'] += pts
            worker['total_tasks'] += 1
            sessions[worker['current_session']]['tasks_done'] += 1
            sessions[worker['current_session']]['points'] += pts
            log_print(f"Task {tid} completed by {worker['hostname']} in {time_taken}s. (+{pts} pts)", "SUCCESS")
            save_worker_info()
        
        # Write to CSV
        with open(WORKER_LOGS, 'a', newline='') as f:
            csv.writer(f).writerow([
                time.time(), wid, worker['current_session'], tid, task['task_type'],
                worker['benchmark_score'], task['assigned_time'], time.time(), time_taken,
                pts, task['status'], task['progress'], data.get('error', '')
            ])
            
    return jsonify({"status": "ok", "points_earned": pts})

# Helper to gen dummy tasks
@app.route('/api/generate_tasks', methods=['POST'])
def generate_tasks():
    import random
    available = [
        ('prime_number', 50000, 10),
        ('matrix_multiplication', 300, 15),
        ('hash_workload', 200000, 5),
        ('sort_arrays', 1000000, 8),
        ('monte_carlo_pi', 200000, 7),
    ]
    with state_lock:
        for _ in range(5):
            plugin_id, work_units, cost = random.choice(available)
            tid = str(uuid.uuid4())[:8]
            tasks[tid] = {
                'task_id': tid,
                'task_type': plugin_id,
                'plugin_id': plugin_id,
                'difficulty': work_units,
                'estimated_compute_cost': cost,
                'status': 'queued',
                'progress': 0,
                'assigned_worker_id': None,
                'retries_count': 0,
                'created_time': time.time()
            }
    log_print('Generated 5 random tasks in queue.', 'INFO')
    return jsonify({'status': 'generated'})

# --- Dashboard APIs ---
@app.route('/api/system_stats')
def system_stats():
    with state_lock:
        return jsonify({
            "workers": list(workers.values()),
            "tasks": list(tasks.values())
        })

@app.route('/api/analysis')
def analysis_data():
    with state_lock:
        workers_list = list(workers.values())
        stats = {
            'total_workers': len(workers_list),
            'active_workers': sum(1 for w in workers_list if w['status'] != 'offline'),
            'offline_workers': sum(1 for w in workers_list if w['status'] == 'offline'),
            'total_points': sum(w.get('total_points', 0) for w in workers_list),
            'total_tasks': sum(w.get('total_tasks', 0) for w in workers_list),
            'task_status': {
                'running': sum(1 for t in tasks.values() if t['status'] == 'running'),
                'queued': sum(1 for t in tasks.values() if t['status'] == 'queued'),
                'completed': sum(1 for t in tasks.values() if t['status'] == 'completed'),
                'failed': sum(1 for t in tasks.values() if t['status'] == 'failed')
            },
            'workers': [
                {
                    'worker_id': w['worker_id'],
                    'hostname': w['hostname'],
                    'status': w['status'],
                    'benchmark_score': w['benchmark_score'],
                    'total_points': w.get('total_points', 0),
                    'total_tasks': w.get('total_tasks', 0),
                    'last_seen': w.get('last_heartbeat', 0),
                    'ip': w.get('ip', ''),
                    'os': w['os'],
                    'cpu': w['cpu'],
                    'known_since': w.get('known_since', 0)
                }
                for w in workers_list
            ]
        }
    return jsonify(stats)

@app.route('/api/plugins')
def plugin_list():
    with state_lock:
        return jsonify(list(plugins.values()))

@app.route('/analysis')
def analysis_page():
    return render_template('analysis.html')

@app.route('/')
@app.route('/dashboard')
def dashboard(): return render_template('index.html')

@app.route('/worker/<wid>')
def worker_page(wid): return render_template('worker.html', wid=wid)

if __name__ == '__main__':
    log_print("Starting Volunteer Cloud Controller...", "INFO")
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port)