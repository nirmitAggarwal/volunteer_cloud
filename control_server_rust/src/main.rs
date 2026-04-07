use actix_files::Files;
use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use chrono::Local;
use colored::*;
use csv::{ReaderBuilder, WriterBuilder};
use rand::seq::SliceRandom;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::sync::{Arc, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};
use tera::{Context, Tera};
use tokio::task;
use uuid::Uuid;

const STORAGE_DIR: &str = "storage";
const WORKER_LOGS: &str = "storage/worker_logs.csv";
const SESSION_LOGS: &str = "storage/session_logs.csv";
const WORKER_INFO: &str = "storage/worker_info.csv";

fn now_ts() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

fn log_print(msg: &str, level: &str) {
    let t = Local::now().format("%H:%M:%S").to_string();
    let prefix = format!("[{}]", t).bright_black();

    let colored_msg = match level {
        "INFO" => msg.cyan(),
        "SUCCESS" => msg.green(),
        "WARN" => msg.yellow(),
        "ERROR" => msg.red(),
        "TASK" => msg.magenta(),
        _ => msg.white(),
    };

    println!("{} {}", prefix, colored_msg);
}

#[derive(Clone, Serialize, Deserialize)]
struct Worker {
    worker_id: String,
    hostname: String,
    os: String,
    cpu: String,
    cores: i32,
    ram: String,
    benchmark_score: i32,
    ip: String,
    status: String,
    last_heartbeat: f64,
    current_task: Option<String>,
    current_session: Option<String>,
    total_points: f64,
    total_tasks: i32,
    known_since: f64,
}

#[derive(Clone, Serialize, Deserialize)]
struct Task {
    task_id: String,
    task_type: String,
    difficulty: i64,
    estimated_compute_cost: i32,
    status: String,
    progress: i32,
    assigned_worker_id: Option<String>,
    retries_count: i32,
    created_time: f64,
    assigned_time: Option<f64>,
    completed_time: Option<f64>,
}

#[derive(Clone, Serialize, Deserialize)]
struct Session {
    connected_at: f64,
    tasks_done: i32,
    points: f64,
}

#[derive(Clone)]
struct AppState {
    workers: Arc<RwLock<HashMap<String, Worker>>>,
    tasks: Arc<RwLock<HashMap<String, Task>>>,
    sessions: Arc<RwLock<HashMap<String, Session>>>,
}

fn init_csv(filepath: &str, headers: &[&str]) {
    if !std::path::Path::new(filepath).exists() {
        let mut wtr = WriterBuilder::new()
            .has_headers(false)
            .from_path(filepath)
            .unwrap();

        wtr.write_record(headers).unwrap();
        wtr.flush().unwrap();
    }
}

fn load_csv_rows(filepath: &str) -> Vec<HashMap<String, String>> {
    if !std::path::Path::new(filepath).exists() {
        return vec![];
    }

    let mut rdr = ReaderBuilder::new().from_path(filepath).unwrap();
    let headers = rdr.headers().unwrap().clone();

    let mut rows = vec![];
    for result in rdr.records() {
        if let Ok(record) = result {
            let mut map = HashMap::new();
            for (i, h) in headers.iter().enumerate() {
                map.insert(h.to_string(), record.get(i).unwrap_or("").to_string());
            }
            rows.push(map);
        }
    }
    rows
}

async fn save_worker_info(state: &AppState) {
    let headers = [
        "worker_id",
        "hostname",
        "os",
        "cpu",
        "cores",
        "ram",
        "benchmark_score",
        "ip",
        "last_seen",
        "total_points",
        "total_tasks",
        "known_since",
    ];

    let records: Vec<Vec<String>>;
    {
        let workers = state.workers.read().unwrap();
        records = workers.values().map(|w| vec![
            w.worker_id.clone(),
            w.hostname.clone(),
            w.os.clone(),
            w.cpu.clone(),
            w.cores.to_string(),
            w.ram.clone(),
            w.benchmark_score.to_string(),
            w.ip.clone(),
            w.last_heartbeat.to_string(),
            w.total_points.to_string(),
            w.total_tasks.to_string(),
            w.known_since.to_string(),
        ]).collect();
    }

    task::spawn_blocking(move || {
        let mut wtr = WriterBuilder::new()
            .has_headers(true)
            .from_path(WORKER_INFO)
            .unwrap();

        wtr.write_record(&headers).unwrap();

        for record in records {
            wtr.write_record(&record).unwrap();
        }

        wtr.flush().unwrap();
    }).await.unwrap();
}

fn load_known_workers(state: &AppState) {
    let rows = load_csv_rows(WORKER_INFO);

    let mut workers = state.workers.write().unwrap();

    for row in rows {
        let worker_id = row.get("worker_id").unwrap_or(&"".to_string()).clone();
        if worker_id.is_empty() {
            continue;
        }

        let cores = row.get("cores").unwrap_or(&"0".to_string()).parse().unwrap_or(0);
        let bench = row
            .get("benchmark_score")
            .unwrap_or(&"0".to_string())
            .parse()
            .unwrap_or(0);
        let points = row
            .get("total_points")
            .unwrap_or(&"0".to_string())
            .parse()
            .unwrap_or(0.0);
        let total_tasks = row
            .get("total_tasks")
            .unwrap_or(&"0".to_string())
            .parse()
            .unwrap_or(0);

        let known_since = row
            .get("known_since")
            .unwrap_or(&"0".to_string())
            .parse()
            .unwrap_or(0.0);

        let worker = Worker {
            worker_id: worker_id.clone(),
            hostname: row.get("hostname").unwrap_or(&"".to_string()).clone(),
            os: row.get("os").unwrap_or(&"".to_string()).clone(),
            cpu: row.get("cpu").unwrap_or(&"".to_string()).clone(),
            cores,
            ram: row.get("ram").unwrap_or(&"".to_string()).clone(),
            benchmark_score: bench,
            ip: row.get("ip").unwrap_or(&"".to_string()).clone(),
            status: "offline".to_string(),
            last_heartbeat: 0.0,
            current_task: None,
            current_session: None,
            total_points: points,
            total_tasks,
            known_since,
        };

        workers.insert(worker_id, worker);
    }
}

fn get_best_task_for_worker(worker: &Worker, state: &AppState) -> Option<Task> {
    let tasks_map = state.tasks.read().unwrap();
    let mut pending: Vec<Task> = tasks_map
        .values()
        .filter(|t| t.status == "queued")
        .cloned()
        .collect();

    if pending.is_empty() {
        return None;
    }

    let workers_map = state.workers.read().unwrap();
    let mut online_bench: Vec<i32> = workers_map
        .values()
        .filter(|w| w.status != "offline")
        .map(|w| w.benchmark_score)
        .collect();

    online_bench.sort();
    let median = if online_bench.is_empty() {
        0
    } else {
        online_bench[online_bench.len() / 2]
    };

    pending.sort_by(|a, b| b.estimated_compute_cost.cmp(&a.estimated_compute_cost));

    if worker.benchmark_score >= median {
        Some(pending[0].clone())
    } else {
        Some(pending[pending.len() - 1].clone())
    }
}

async fn fault_tolerance_loop(state: AppState) {
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
        let current_time = now_ts();
        let mut updated = false;

        {
            let mut workers = state.workers.write().unwrap();
            let mut tasks = state.tasks.write().unwrap();
            let sessions = state.sessions.read().unwrap();

            for (wid, w) in workers.iter_mut() {
                if w.status == "offline" {
                    continue;
                }

                if current_time - w.last_heartbeat > 6.0 {
                    log_print(
                        &format!(
                            "Worker {} ({}) missed heartbeat. Marking offline.",
                            w.hostname, wid
                        ),
                        "ERROR",
                    );

                    w.status = "offline".to_string();
                    updated = true;

                    if let Some(sess_id) = &w.current_session {
                        if let Some(session) = sessions.get(sess_id) {
                            let uptime = current_time - session.connected_at;
                            let record = vec![
                                wid.clone(),
                                sess_id.clone(),
                                session.connected_at.to_string(),
                                current_time.to_string(),
                                uptime.to_string(),
                                session.tasks_done.to_string(),
                                session.points.to_string(),
                            ];

                            task::spawn_blocking(move || {
                                let file = OpenOptions::new()
                                    .create(true)
                                    .append(true)
                                    .open(SESSION_LOGS)
                                    .unwrap();

                                let mut wtr = WriterBuilder::new().has_headers(false).from_writer(file);
                                wtr.write_record(&record).unwrap();
                                wtr.flush().unwrap();
                            });
                        }
                    }

                    if let Some(tid) = &w.current_task {
                        if let Some(task) = tasks.get_mut(tid) {
                            if task.status == "running" {
                                task.status = "queued".to_string();
                                task.retries_count += 1;
                                task.assigned_worker_id = None;
                                log_print(
                                    &format!(
                                        "Task {} requeued (Worker failed). Progress was {}%",
                                        tid, task.progress
                                    ),
                                    "WARN",
                                );
                            }
                        }
                    }

                    w.current_task = None;
                }
            }
        }

        if updated {
            save_worker_info(&state).await;
        }
    }
}

#[derive(Deserialize)]
struct RegisterWorkerReq {
    worker_id: String,
    hostname: String,
    os: String,
    cpu: String,
    cores: i32,
    ram: String,
    benchmark_score: i32,
}

async fn register_worker(
    req: HttpRequest,
    state: web::Data<AppState>,
    data: web::Json<RegisterWorkerReq>,
) -> impl Responder {
    let wid = data.worker_id.clone();
    let session_id = Uuid::new_v4().to_string();
    let ip = req
        .peer_addr()
        .map(|a| a.ip().to_string())
        .unwrap_or("".to_string());

    {
        let mut workers = state.workers.write().unwrap();
        let mut sessions = state.sessions.write().unwrap();

        if !workers.contains_key(&wid) {
            workers.insert(
                wid.clone(),
                Worker {
                    worker_id: wid.clone(),
                    hostname: "".to_string(),
                    os: "".to_string(),
                    cpu: "".to_string(),
                    cores: 0,
                    ram: "".to_string(),
                    benchmark_score: 0,
                    ip: "".to_string(),
                    status: "offline".to_string(),
                    last_heartbeat: 0.0,
                    current_task: None,
                    current_session: None,
                    total_points: 0.0,
                    total_tasks: 0,
                    known_since: now_ts(),
                },
            );
        }

        let w = workers.get_mut(&wid).unwrap();
        w.hostname = data.hostname.clone();
        w.os = data.os.clone();
        w.cpu = data.cpu.clone();
        w.cores = data.cores;
        w.ram = data.ram.clone();
        w.benchmark_score = data.benchmark_score;
        w.ip = ip;
        w.status = "online".to_string();
        w.last_heartbeat = now_ts();
        w.current_task = None;
        w.current_session = Some(session_id.clone());

        sessions.insert(
            session_id.clone(),
            Session {
                connected_at: now_ts(),
                tasks_done: 0,
                points: 0.0,
            },
        );
    }

    save_worker_info(&state).await;

    log_print(
        &format!(
            "Worker Connected: {} (Bench: {})",
            data.hostname, data.benchmark_score
        ),
        "SUCCESS",
    );

    HttpResponse::Ok().json(serde_json::json!({
        "status": "registered",
        "session_id": session_id
    }))
}

#[derive(Deserialize)]
struct HeartbeatReq {
    worker_id: String,
}

async fn heartbeat(state: web::Data<AppState>, data: web::Json<HeartbeatReq>) -> impl Responder {
    let wid = data.worker_id.clone();

    let mut workers = state.workers.write().unwrap();
    if let Some(w) = workers.get_mut(&wid) {
        w.last_heartbeat = now_ts();
        if w.status == "offline" {
            w.status = "online".to_string();
        }
        return HttpResponse::Ok().json(serde_json::json!({"status": "ok"}));
    }

    HttpResponse::NotFound().json(serde_json::json!({"error": "Worker not registered"}))
}

#[derive(Deserialize)]
struct RequestTaskReq {
    worker_id: String,
}

async fn request_task(state: web::Data<AppState>, data: web::Json<RequestTaskReq>) -> impl Responder {
    let wid = data.worker_id.clone();

    let worker_opt = {
        let workers = state.workers.read().unwrap();
        workers.get(&wid).cloned()
    };

    if worker_opt.is_none() {
        return HttpResponse::Ok().json(serde_json::json!({"task": null}));
    }

    let worker = worker_opt.unwrap();
    if worker.status == "offline" || worker.current_task.is_some() {
        return HttpResponse::Ok().json(serde_json::json!({"task": null}));
    }

    let chosen = get_best_task_for_worker(&worker, &state);

    if let Some(mut task) = chosen {
        {
            let mut tasks = state.tasks.write().unwrap();
            let mut workers = state.workers.write().unwrap();

            if let Some(t) = tasks.get_mut(&task.task_id) {
                t.status = "running".to_string();
                t.assigned_worker_id = Some(wid.clone());
                t.assigned_time = Some(now_ts());
                task = t.clone();
            }

            if let Some(w) = workers.get_mut(&wid) {
                w.current_task = Some(task.task_id.clone());
                w.status = "busy".to_string();
            }
        }

        let hostname = {
            let workers = state.workers.read().unwrap();
            workers.get(&wid).map(|w| w.hostname.clone()).unwrap_or("unknown".to_string())
        };

        log_print(&format!("Assigned {} to {}", task.task_type, hostname), "TASK");

        return HttpResponse::Ok().json(serde_json::json!({"task": task}));
    }

    HttpResponse::Ok().json(serde_json::json!({"task": null}))
}

#[derive(Deserialize)]
struct ProgressUpdateReq {
    task_id: String,
    progress: i32,
}

async fn progress_update(
    state: web::Data<AppState>,
    data: web::Json<ProgressUpdateReq>,
) -> impl Responder {
    let mut tasks = state.tasks.write().unwrap();
    if let Some(task) = tasks.get_mut(&data.task_id) {
        task.progress = data.progress;
    }

    HttpResponse::Ok().json(serde_json::json!({"status": "ok"}))
}

#[derive(Deserialize)]
struct TaskResultReq {
    worker_id: String,
    task_id: String,
    success: bool,
    time_taken: f64,
    error: Option<String>,
}

async fn task_result(state: web::Data<AppState>, data: web::Json<TaskResultReq>) -> impl Responder {
    let wid = data.worker_id.clone();
    let tid = data.task_id.clone();

    let mut pts = 0.0;
    let mut save_worker = false;
    let record: Vec<String>;

    {
        let mut tasks = state.tasks.write().unwrap();
        let mut workers = state.workers.write().unwrap();
        let mut mut_sessions = state.sessions.write().unwrap();

        let task = tasks.get_mut(&tid);
        let worker = workers.get_mut(&wid);

        if task.is_none() || worker.is_none() {
            return HttpResponse::BadRequest().json(serde_json::json!({"error": "Invalid state"}));
        }

        let task = task.unwrap();
        let worker = worker.unwrap();

        if data.success {
            pts = (worker.benchmark_score as f64 * data.time_taken) / 100.0;
        }

        task.status = if data.success { "completed".to_string() } else { "failed".to_string() };
        task.progress = if data.success { 100 } else { task.progress };
        task.completed_time = Some(now_ts());

        worker.current_task = None;
        worker.status = "online".to_string();

        if data.success {
            worker.total_points += pts;
            worker.total_tasks += 1;

            if let Some(sess_id) = &worker.current_session {
                if let Some(sess) = mut_sessions.get_mut(sess_id) {
                    sess.tasks_done += 1;
                    sess.points += pts;
                }
            }

            log_print(
                &format!(
                    "Task {} completed by {} in {}s. (+{} pts)",
                    tid, worker.hostname, data.time_taken, pts
                ),
                "SUCCESS",
            );

            save_worker = true;
        }

        let session_id = worker.current_session.clone().unwrap_or_else(|| "".to_string());
        let assigned_time = task.assigned_time.unwrap_or(0.0);

        record = vec![
            now_ts().to_string(),
            wid.clone(),
            session_id,
            tid.clone(),
            task.task_type.clone(),
            worker.benchmark_score.to_string(),
            assigned_time.to_string(),
            now_ts().to_string(),
            data.time_taken.to_string(),
            pts.to_string(),
            task.status.clone(),
            task.progress.to_string(),
            data.error.clone().unwrap_or_else(|| "".to_string()),
        ];
    }

    if save_worker {
        save_worker_info(&state).await;
    }

    task::spawn_blocking(move || {
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(WORKER_LOGS)
            .unwrap();

        let mut wtr = WriterBuilder::new().has_headers(false).from_writer(file);
        wtr.write_record(&record).unwrap();
        wtr.flush().unwrap();
    });

    HttpResponse::Ok().json(serde_json::json!({
        "status": "ok",
        "points_earned": pts
    }))
}

async fn generate_tasks(state: web::Data<AppState>) -> impl Responder {
    let types = vec![
        ("prime_number", 50000_i64, 10_i32),
        ("matrix_multiplication", 300_i64, 15_i32),
        ("hash_workload", 200000_i64, 5_i32),
        ("sort_arrays", 1000000_i64, 8_i32),
    ];

    let mut rng = rand::thread_rng();

    {
        let mut tasks = state.tasks.write().unwrap();

        for _ in 0..5 {
            let (t_type, work_units, cost) = types.choose(&mut rng).unwrap();
            let tid = Uuid::new_v4().to_string();
            let tid_short = tid[..8].to_string();

            tasks.insert(
                tid_short.clone(),
                Task {
                    task_id: tid_short,
                    task_type: t_type.to_string(),
                    difficulty: *work_units,
                    estimated_compute_cost: *cost,
                    status: "queued".to_string(),
                    progress: 0,
                    assigned_worker_id: None,
                    retries_count: 0,
                    created_time: now_ts(),
                    assigned_time: None,
                    completed_time: None,
                },
            );
        }
    }

    log_print("Generated 5 random tasks in queue.", "INFO");
    HttpResponse::Ok().json(serde_json::json!({"status": "generated"}))
}

async fn system_stats(state: web::Data<AppState>) -> impl Responder {
    let workers = state.workers.read().unwrap();
    let tasks = state.tasks.read().unwrap();

    let workers_vec: Vec<Worker> = workers.values().cloned().collect();
    let tasks_vec: Vec<Task> = tasks.values().cloned().collect();

    HttpResponse::Ok().json(serde_json::json!({
        "workers": workers_vec,
        "tasks": tasks_vec
    }))
}

async fn analysis_data(state: web::Data<AppState>) -> impl Responder {
    let workers = state.workers.read().unwrap();
    let tasks = state.tasks.read().unwrap();

    let workers_list: Vec<Worker> = workers.values().cloned().collect();

    let total_workers = workers_list.len() as i32;
    let active_workers = workers_list.iter().filter(|w| w.status != "offline").count() as i32;
    let offline_workers = workers_list.iter().filter(|w| w.status == "offline").count() as i32;

    let total_points: f64 = workers_list.iter().map(|w| w.total_points).sum();
    let total_tasks_done: i32 = workers_list.iter().map(|w| w.total_tasks).sum();

    let running = tasks.values().filter(|t| t.status == "running").count() as i32;
    let queued = tasks.values().filter(|t| t.status == "queued").count() as i32;
    let completed = tasks.values().filter(|t| t.status == "completed").count() as i32;
    let failed = tasks.values().filter(|t| t.status == "failed").count() as i32;

    let workers_json: Vec<serde_json::Value> = workers_list
        .iter()
        .map(|w| {
            serde_json::json!({
                "worker_id": w.worker_id,
                "hostname": w.hostname,
                "status": w.status,
                "benchmark_score": w.benchmark_score,
                "total_points": w.total_points,
                "total_tasks": w.total_tasks,
                "last_seen": w.last_heartbeat,
                "ip": w.ip,
                "os": w.os,
                "cpu": w.cpu,
                "known_since": w.known_since
            })
        })
        .collect();

    HttpResponse::Ok().json(serde_json::json!({
        "total_workers": total_workers,
        "active_workers": active_workers,
        "offline_workers": offline_workers,
        "total_points": total_points,
        "total_tasks": total_tasks_done,
        "task_status": {
            "running": running,
            "queued": queued,
            "completed": completed,
            "failed": failed
        },
        "workers": workers_json
    }))
}

async fn analysis_page(tmpl: web::Data<Tera>) -> impl Responder {
    let ctx = Context::new();
    let html = tmpl.render("analysis.html", &ctx).unwrap();
    HttpResponse::Ok().content_type("text/html").body(html)
}

async fn dashboard(tmpl: web::Data<Tera>) -> impl Responder {
    let ctx = Context::new();
    let html = tmpl.render("index.html", &ctx).unwrap();
    HttpResponse::Ok().content_type("text/html").body(html)
}

async fn worker_page(tmpl: web::Data<Tera>, path: web::Path<String>) -> impl Responder {
    let wid = path.into_inner();
    let mut ctx = Context::new();
    ctx.insert("wid", &wid);

    let html = tmpl.render("worker.html", &ctx).unwrap();
    HttpResponse::Ok().content_type("text/html").body(html)
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    fs::create_dir_all(STORAGE_DIR).unwrap();

    init_csv(
        WORKER_LOGS,
        &[
            "timestamp",
            "worker_id",
            "session_id",
            "task_id",
            "task_type",
            "benchmark_score",
            "start_time",
            "end_time",
            "time_taken",
            "points_earned",
            "status",
            "progress_last_seen",
            "error_message",
        ],
    );

    init_csv(
        SESSION_LOGS,
        &[
            "worker_id",
            "session_id",
            "connected_at",
            "disconnected_at",
            "uptime_seconds",
            "total_tasks_done",
            "total_points",
        ],
    );

    init_csv(
        WORKER_INFO,
        &[
            "worker_id",
            "hostname",
            "os",
            "cpu",
            "cores",
            "ram",
            "benchmark_score",
            "ip",
            "last_seen",
            "total_points",
            "total_tasks",
            "known_since",
        ],
    );

    let state = AppState {
        workers: Arc::new(RwLock::new(HashMap::new())),
        tasks: Arc::new(RwLock::new(HashMap::new())),
        sessions: Arc::new(RwLock::new(HashMap::new())),
    };

    load_known_workers(&state);

    let state_clone = state.clone();
    tokio::spawn(async move {
        fault_tolerance_loop(state_clone).await;
    });

    log_print("Starting Volunteer Cloud Controller...", "INFO");

    let tera = Tera::new("templates/**/*").unwrap();

    HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(state.clone()))
            .app_data(web::Data::new(tera.clone()))
            .service(Files::new("/static", "static").show_files_listing())
            .route("/", web::get().to(dashboard))
            .route("/dashboard", web::get().to(dashboard))
            .route("/analysis", web::get().to(analysis_page))
            .route("/worker/{wid}", web::get().to(worker_page))
            .route("/api/register_worker", web::post().to(register_worker))
            .route("/api/heartbeat", web::post().to(heartbeat))
            .route("/api/request_task", web::post().to(request_task))
            .route("/api/progress_update", web::post().to(progress_update))
            .route("/api/task_result", web::post().to(task_result))
            .route("/api/generate_tasks", web::post().to(generate_tasks))
            .route("/api/system_stats", web::get().to(system_stats))
            .route("/api/analysis", web::get().to(analysis_data))
    })
    .bind(("0.0.0.0", 3000))?
    .run()
    .await
}