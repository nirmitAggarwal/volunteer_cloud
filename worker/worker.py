import os
import sys
import time
import uuid
import json
import psutil
import socket
import platform
import hashlib
import subprocess
import requests
import threading
import random
from pathlib import Path
from typing import cast
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from colorama import init, Fore, Style

# Initialize terminal colors
init(autoreset=True)

# ==========================================
# CONFIGURATION
# ==========================================
# Change this to the IP of the Server Laptop or set VOLUNTEER_SERVER_URL
SERVER_URL = os.environ.get("VOLUNTEER_SERVER_URL", "https://volunteer-cloud.onrender.com/")
WORKER_ID = str(uuid.uuid4())[:8]

# ==========================================
# SYSTEM & BENCHMARKING
# ==========================================
def run_benchmark():
    print(f"{Fore.CYAN}Running hardware benchmark...{Style.RESET_ALL}")
    start = time.time()
    # A mix of math and hashing to test single-core burst capability
    for i in range(20000):
        _ = hashlib.sha256(str(i).encode()).hexdigest()
    end = time.time()
    score = int(2000 / (end - start))
    print(f"{Fore.GREEN}Benchmark Complete! Score: {score}{Style.RESET_ALL}")
    return score

def get_specs():
    return {
        "worker_id": WORKER_ID,
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or "Unknown CPU",
        "cores": psutil.cpu_count(logical=True),
        "ram": f"{round(psutil.virtual_memory().total / (1024**3), 1)} GB",
        "benchmark_score": run_benchmark()
    }

# ==========================================
# PLUGIN MANAGEMENT
# ==========================================
ADMIN_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAHekZvoRFgefh0hkfcBcVFdMiiycmPm3wSy/7+Fya08Y=\n-----END PUBLIC KEY-----\n"""
PLUGIN_CACHE_DIR = Path(__file__).resolve().parent / 'plugin_cache'
PLUGIN_CACHE_DIR.mkdir(exist_ok=True)
JOB_DIR = Path(__file__).resolve().parent / 'job_runs'
JOB_DIR.mkdir(exist_ok=True)


def load_public_key():
    public_key = load_pem_public_key(ADMIN_PUBLIC_KEY_PEM.encode('utf-8'))
    public_key = cast(Ed25519PublicKey, public_key)
    if not isinstance(public_key, Ed25519PublicKey):
        raise RuntimeError('Loaded public key is not Ed25519')
    return public_key


def verify_manifest_signature(manifest_data, signature_bytes):
    try:
        public_key = load_public_key()
        # Canonicalize the manifest data before verifying to match server-side signing
        encoded = json.dumps(manifest_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
        public_key.verify(signature_bytes, encoded)
        return True
    except InvalidSignature:
        return False


def download_file(url, dest_path, binary=True):
    if url.startswith('/'):
        url = f"{SERVER_URL.rstrip('/')}{url}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    mode = 'wb' if binary else 'w'
    encoding = None if binary else 'utf-8'
    with open(dest_path, mode, encoding=encoding) as f:
        f.write(resp.content if binary else resp.text)


def ensure_plugin_bundle(task):
    plugin_id = task.get('plugin_id') or task.get('task_type')
    if not plugin_id:
        raise RuntimeError('No plugin_id on task')

    plugin_dir = PLUGIN_CACHE_DIR / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = plugin_dir / 'manifest.json'
    signature_path = plugin_dir / 'manifest.sig'
    plugin_path = plugin_dir / task.get('entrypoint', 'plugin.py')

    if not manifest_path.exists() or not signature_path.exists() or not plugin_path.exists():
        download_file(task['manifest_url'], manifest_path, binary=False)
        download_file(task['signature_url'], signature_path, binary=True)
        download_file(task['plugin_url'], plugin_path, binary=True)

    try:
        manifest_bytes = manifest_path.read_bytes()
        signature_bytes = signature_path.read_bytes()
        
        manifest = json.loads(manifest_bytes.decode('utf-8'))
        if not verify_manifest_signature(manifest, signature_bytes):
            raise RuntimeError('Manifest signature verification failed')

        plugin_hash = hashlib.sha256(plugin_path.read_bytes()).hexdigest()
        if plugin_hash != manifest['sha256']:
            raise RuntimeError('Plugin hash mismatch')
            
    except (RuntimeError, json.JSONDecodeError, InvalidSignature) as e:
        # Clear cache on verification failure so we re-download next time
        print(f"{Fore.RED}Verification failed, clearing cache for {plugin_id}: {e}{Style.RESET_ALL}")
        if manifest_path.exists(): manifest_path.unlink()
        if signature_path.exists(): signature_path.unlink()
        if plugin_path.exists(): plugin_path.unlink()
        raise e

    return plugin_path, manifest


def send_progress(task_id, progress):
    try:
        requests.post(f"{SERVER_URL}/api/progress_update", json={"task_id": task_id, "progress": progress}, timeout=2)
    except Exception:
        pass


def execute_plugin(task):
    plugin_path, manifest = ensure_plugin_bundle(task)
    task_folder = JOB_DIR / task['task_id']
    task_folder.mkdir(parents=True, exist_ok=True)
    output_path = task_folder / 'output.json'

    cmd = [sys.executable, str(plugin_path), '--difficulty', str(task['difficulty']), '--output', str(output_path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_time = time.time()
    timeout_sec = task.get('timeout_sec', manifest.get('timeout_sec', 120))

    stdout = proc.stdout
    if stdout is None:
        proc.kill()
        raise RuntimeError('Plugin stdout is unavailable')

    while True:
        line = stdout.readline()
        if line:
            if line.startswith('PROGRESS:'):
                try:
                    pct = int(line.split(':', 1)[1].strip())
                    send_progress(task['task_id'], pct)
                except ValueError:
                    pass
            else:
                print(line.strip())
        elif proc.poll() is not None:
            break

        if time.time() - start_time > timeout_sec:
            proc.kill()
            raise RuntimeError('Plugin execution timed out')

    if proc.returncode != 0:
        raise RuntimeError(f'Plugin exited with code {proc.returncode}')

    return output_path

# ==========================================
# COMMUNICATION LOOPS
# ==========================================
def heartbeat_loop():
    """Pings the server every 2 seconds to prove we are alive."""
    while True:
        try:
            requests.post(f"{SERVER_URL}/api/heartbeat", json={"worker_id": WORKER_ID}, timeout=2)
        except:
            pass # Fail silently, fault tolerance will handle it
        time.sleep(2)

def task_loop():
    """Polls for tasks and executes them."""
    while True:
        time.sleep(1) # Polling interval
        try:
            res = requests.post(f"{SERVER_URL}/api/request_task", json={"worker_id": WORKER_ID}, timeout=3)
            if res.status_code != 200: continue
            
            data = res.json()
            task = data.get('task')
            if not task: continue # No tasks available
            
            print(f"\n{Fore.YELLOW}[+] Received Task:{Style.RESET_ALL} {task['task_type']} (ID: {task['task_id']})")
            start_t = time.time()
            tid = task['task_id']
            diff = task['difficulty']
            
            try:
                output_path = execute_plugin(task)
                duration = round(time.time() - start_t, 2)

                requests.post(f"{SERVER_URL}/api/task_result", json={
                    "worker_id": WORKER_ID,
                    "task_id": tid,
                    "success": True,
                    "time_taken": duration,
                    "output_file": str(output_path)
                })
                print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} Task completed in {duration}s")
            except Exception as e:
                print(f"{Fore.RED}[ERROR] Task failed: {e}{Style.RESET_ALL}")
                requests.post(f"{SERVER_URL}/api/task_result", json={
                    "worker_id": WORKER_ID,
                    "task_id": tid,
                    "success": False,
                    "time_taken": 0,
                    "error": str(e)
                })

        except requests.exceptions.RequestException:
            print(f"{Fore.RED}Connection to server lost. Retrying...{Style.RESET_ALL}", end='\r')
            try:
                time.sleep(3)
            except KeyboardInterrupt:
                print(f"\n{Fore.YELLOW}Worker interrupted by user. Exiting...{Style.RESET_ALL}")
                break
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Worker interrupted by user. Exiting...{Style.RESET_ALL}")
            break

# ==========================================
# MAIN ENTRY POINT
# ==========================================
if __name__ == '__main__':
    print(f"{Fore.BLUE}======================================{Style.RESET_ALL}")
    print(f"{Fore.BLUE}     ☁️ VOLUNTEER CLOUD WORKER ☁️      {Style.RESET_ALL}")
    print(f"{Fore.BLUE}======================================{Style.RESET_ALL}")
    
    specs = get_specs()
    print(f"Device: {specs['hostname']} | Cores: {specs['cores']} | RAM: {specs['ram']}")
    
    # 1. Registration Loop
    while True:
        try:
            print(f"Attempting to connect to {SERVER_URL}...")
            r = requests.post(f"{SERVER_URL}/api/register_worker", json=specs, timeout=5)
            if r.status_code == 200:
                print(f"{Fore.GREEN}Registered successfully! Node ID: {WORKER_ID}{Style.RESET_ALL}")
                break
        except requests.exceptions.RequestException:
            print(f"{Fore.RED}Server unreachable. Retrying in 5 seconds...{Style.RESET_ALL}")
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                print(f"\n{Fore.YELLOW}Worker interrupted by user during retry. Exiting...{Style.RESET_ALL}")
                sys.exit(0)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Worker interrupted by user. Exiting...{Style.RESET_ALL}")
            sys.exit(0)
            
    # 2. Start Threads
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    try:
        task_loop()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Worker interrupted by user. Exiting...{Style.RESET_ALL}")