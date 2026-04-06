import os
import time
import uuid
import psutil
import socket
import platform
import hashlib
import requests
import threading
import random
from colorama import init, Fore, Style

# Initialize terminal colors
init(autoreset=True)

# ==========================================
# CONFIGURATION
# ==========================================
# Change this to the IP of the Server Laptop
SERVER_URL = "http://127.0.0.1:3000" 
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
# TASK WORKLOAD FUNCTIONS
# ==========================================
def compute_primes(limit, update_progress):
    """CPU-heavy integer math."""
    primes = []
    for i in range(2, limit):
        if i % (limit // 10) == 0: 
            update_progress(int((i / limit) * 100))
        is_p = True
        for j in range(2, int(i**0.5) + 1):
            if i % j == 0:
                is_p = False
                break
        if is_p: primes.append(i)
    return len(primes)

def compute_matrix(size, update_progress):
    """Memory bandwidth and floating-point heavy."""
    mat1 = [[random.random() for _ in range(size)] for _ in range(size)]
    mat2 = [[random.random() for _ in range(size)] for _ in range(size)]
    res = [[0]*size for _ in range(size)]
    
    for i in range(size):
        if i % (size // 10) == 0: 
            update_progress(int((i / size) * 100))
        for j in range(size):
            for k in range(size): 
                res[i][j] += mat1[i][k] * mat2[k][j]
    return "Matrix Computed"

def compute_hash(iterations, update_progress):
    """Cryptographic ALU stress test."""
    last_hash = "start"
    for i in range(iterations):
        if i % (iterations // 10) == 0: 
            update_progress(int((i / iterations) * 100))
        last_hash = hashlib.sha256((last_hash + str(i)).encode()).hexdigest()
    return last_hash

def compute_sort(array_size, update_progress):
    """Memory allocation and sorting algorithm stress."""
    update_progress(10)
    arr = [random.randint(1, 100000) for _ in range(array_size)]
    update_progress(40)
    arr.sort() # Python's Timsort
    update_progress(90)
    return "Array Sorted"

def compute_monte_carlo_pi(iterations, update_progress):
    """Stochastic simulation for Pi estimation."""
    inside_circle = 0
    for i in range(iterations):
        if i % (iterations // 10) == 0: 
            update_progress(int((i / iterations) * 100))
        x, y = random.random(), random.random()
        if x**2 + y**2 <= 1:
            inside_circle += 1
    
    pi_estimate = (inside_circle / iterations) * 4
    return pi_estimate

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
            
            # Helper function to send progress to server
            def update_prog(pct):
                try: 
                    requests.post(f"{SERVER_URL}/api/progress_update", json={"task_id": tid, "progress": pct}, timeout=2)
                except: 
                    pass

            # Execute the specific task type
            try:
                if task['task_type'] == 'prime_number': 
                    compute_primes(diff, update_prog)
                elif task['task_type'] == 'matrix_multiplication': 
                    compute_matrix(diff, update_prog)
                elif task['task_type'] == 'hash_workload': 
                    compute_hash(diff, update_prog)
                elif task['task_type'] == 'sort_arrays': 
                    compute_sort(diff, update_prog)
                elif task['task_type'] == 'monte_carlo_pi':
                    compute_monte_carlo_pi(diff, update_prog)
                else:
                    # Fallback for unknown tasks
                    time.sleep(2)
                    update_prog(100)
                
                duration = round(time.time() - start_t, 2)
                
                # Send Success Result
                requests.post(f"{SERVER_URL}/api/task_result", json={
                    "worker_id": WORKER_ID, 
                    "task_id": tid, 
                    "success": True, 
                    "time_taken": duration
                })
                print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} Task completed in {duration}s")
                
            except Exception as e:
                # Handle unexpected math/memory errors without crashing worker
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
            time.sleep(3)

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
            time.sleep(5)
            
    # 2. Start Threads
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    task_loop()