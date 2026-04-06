import os
import csv
import time
import uuid
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from colorama import init, Fore, Style

# Initialize terminal colors
init(autoreset=True)

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.disabled = True # Disable default flask logs for custom clean logs

STORAGE_DIR = 'storage'
WORKER_LOGS = os.path.join(STORAGE_DIR, 'worker_logs.csv')
SESSION_LOGS = os.path.join(STORAGE_DIR, 'session_logs.csv')
os.makedirs(STORAGE_DIR, exist_ok=True)

# In-Memory State
state_lock = threading.Lock()
workers = {} # worker_id -> dict
tasks = {}   # task_id -> dict
sessions = {} # session_id -> dict

# --- CSV Initialization ---
def init_csv(filepath, headers):
    if not os.path.exists(filepath):
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)

init_csv(WORKER_LOGS, ['timestamp', 'worker_id', 'session_id', 'task_id', 'task_type', 'benchmark_score', 'start_time', 'end_time', 'time_taken', 'points_earned', 'status', 'progress_last_seen', 'error_message'])
init_csv(SESSION_LOGS, ['worker_id', 'session_id', 'connected_at', 'disconnected_at', 'uptime_seconds', 'total_tasks_done', 'total_points'])

def log_print(msg, level="INFO"):
    colors = {"INFO": Fore.CYAN, "SUCCESS": Fore.GREEN, "WARN": Fore.YELLOW, "ERROR": Fore.RED, "TASK": Fore.MAGENTA}
    color = colors.get(level, Fore.WHITE)
    print(f"{Fore.LIGHTBLACK_EX}[{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL} {color}{msg}")

# --- Background Fault Tolerance Thread ---
def fault_tolerance_loop():
    while True:
        time.sleep(2)
        current_time = time.time()
        with state_lock:
            for wid, w in list(workers.items()):
                if w['status'] == 'offline': continue
                
                # Check Heartbeat
                if current_time - w['last_heartbeat'] > 6:
                    log_print(f"Worker {w['hostname']} ({wid}) missed heartbeat. Marking offline.", "ERROR")
                    w['status'] = 'offline'
                    
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
@app.route('/api/register_worker', methods=['POST'])
def register_worker():
    data = request.json
    wid = data['worker_id']
    session_id = str(uuid.uuid4())
    
    with state_lock:
        if wid not in workers:
            workers[wid] = {"total_points": 0, "total_tasks": 0}
        
        workers[wid].update({
            "worker_id": wid, "hostname": data['hostname'], "os": data['os'],
            "cpu": data['cpu'], "cores": data['cores'], "ram": data['ram'],
            "benchmark_score": data['benchmark_score'], "ip": request.remote_addr,
            "status": "online", "last_heartbeat": time.time(),
            "current_task": None, "current_session": session_id
        })
        
        sessions[session_id] = {"connected_at": time.time(), "tasks_done": 0, "points": 0}
    
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
            return jsonify({"task": task})
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
        
        pts = worker['benchmark_score'] * time_taken if success else 0
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
    types = [
        ("prime_number", 50000, 10), ("matrix_multiplication", 300, 15), 
        ("hash_workload", 200000, 5), ("sort_arrays", 1000000, 8)
    ]
    with state_lock:
        for _ in range(5):
            t_type, work_units, cost = random.choice(types)
            tid = str(uuid.uuid4())[:8]
            tasks[tid] = {
                "task_id": tid, "task_type": t_type, "difficulty": work_units,
                "estimated_compute_cost": cost, "status": "queued", "progress": 0,
                "assigned_worker_id": None, "retries_count": 0, "created_time": time.time()
            }
    log_print("Generated 5 random tasks in queue.", "INFO")
    return jsonify({"status": "generated"})

# --- Dashboard APIs ---
@app.route('/api/system_stats')
def system_stats():
    with state_lock:
        return jsonify({
            "workers": list(workers.values()),
            "tasks": list(tasks.values())
        })

@app.route('/')
@app.route('/dashboard')
def dashboard(): return render_template('index.html')

@app.route('/worker/<wid>')
def worker_page(wid): return render_template('worker.html', wid=wid)

if __name__ == '__main__':
    log_print("Starting Volunteer Cloud Controller...", "INFO")
    app.run(host='0.0.0.0', port=3000, threaded=True)