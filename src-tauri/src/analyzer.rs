use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{Manager, State};

#[cfg(windows)]
const PYTHON_NAME: &str = "python.exe";
#[cfg(all(windows, debug_assertions))]
const VENV_BIN_DIRECTORY: &str = "Scripts";
#[cfg(not(windows))]
const PYTHON_NAME: &str = "python";
#[cfg(all(not(windows), debug_assertions))]
const VENV_BIN_DIRECTORY: &str = "bin";

const SCHEMA_VERSION: u32 = 1;
const PROJECT_KIND: &str = "lol-translator-project";

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Region {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl Region {
    fn validate(&self) -> Result<(), String> {
        if [self.x, self.y, self.width, self.height]
            .iter()
            .any(|v| !v.is_finite())
            || self.x < 0.0
            || self.y < 0.0
            || self.width <= 0.0
            || self.height <= 0.0
            || self.x + self.width > 1.0 + 1e-9
            || self.y + self.height > 1.0 + 1e-9
        {
            return Err("字幕範囲は映像内の、面積がある正規化座標で指定してください。".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct LegacyAnalysisRequest {
    video_path: PathBuf,
    subtitle_region: Region,
}

impl LegacyAnalysisRequest {
    fn validate(&self) -> Result<(), String> {
        validate_video(&self.video_path)?;
        self.subtitle_region.validate()
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RangeMode {
    Range,
    Whole,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AnalysisRange {
    mode: RangeMode,
    start_seconds: Option<f64>,
    end_seconds: Option<f64>,
}

impl AnalysisRange {
    fn validate(&self) -> Result<(), String> {
        match self.mode {
            RangeMode::Whole => {
                if self.start_seconds.is_some() || self.end_seconds.is_some() {
                    return Err("動画全体を解析する場合、開始・終了時刻は空にしてください。".into());
                }
            }
            RangeMode::Range => {
                let (Some(start), Some(end)) = (self.start_seconds, self.end_seconds) else {
                    return Err("解析区間の開始時刻と終了時刻を指定してください。".into());
                };
                if !start.is_finite() || !end.is_finite() || start < 0.0 || end <= start {
                    return Err("解析区間は0秒以上で、終了を開始より後にしてください。".into());
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AnalysisSettings {
    sample_interval_ms: u32,
    line_split_ratio: Option<f64>,
}

impl AnalysisSettings {
    fn validate(&self) -> Result<(), String> {
        if !(50..=5_000).contains(&self.sample_interval_ms) {
            return Err("抽出間隔は50〜5000msで指定してください。".into());
        }
        if self
            .line_split_ratio
            .is_some_and(|value| !value.is_finite() || !(0.1..=0.9).contains(&value))
        {
            return Err("2行の分割位置は10〜90%で指定してください。".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeAnalysisRequest {
    schema_version: u32,
    video_path: PathBuf,
    subtitle_region: Region,
    analysis_range: AnalysisRange,
    settings: AnalysisSettings,
}

impl RuntimeAnalysisRequest {
    fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err("対応していない解析設定のバージョンです。".into());
        }
        validate_video(&self.video_path)?;
        self.subtitle_region.validate()?;
        self.analysis_range.validate()?;
        self.settings.validate()
    }
}

fn validate_video(path: &Path) -> Result<(), String> {
    if !path.is_absolute() || !path.is_file() {
        return Err("動画のパスが不正か、ファイルが存在しません。".into());
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct StartedJob {
    job_id: String,
}

#[derive(Debug, Serialize)]
pub struct JobSnapshot {
    job_id: String,
    progress: Value,
    project: Option<Value>,
}

struct AnalysisJob {
    child: Child,
    progress_path: PathBuf,
    result_path: PathBuf,
    cancel_path: PathBuf,
    stderr_path: PathBuf,
}

#[derive(Default)]
pub struct AnalyzerState {
    jobs: Mutex<HashMap<String, AnalysisJob>>,
}

fn unique_name(prefix: &str) -> Result<String, String> {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?
        .as_nanos();
    Ok(format!("{prefix}-{}-{stamp}", std::process::id()))
}

fn atomic_write_json(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "保存先ディレクトリを確認できません。".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("保存先を作成できません: {error}"))?;
    let temporary = path.with_extension(format!(
        "{}.tmp",
        path.extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("json")
    ));
    let mut file = File::create(&temporary)
        .map_err(|error| format!("一時ファイルを作成できません: {error}"))?;
    serde_json::to_writer_pretty(&mut file, value)
        .map_err(|error| format!("JSONを保存できません: {error}"))?;
    file.write_all(b"\n")
        .map_err(|error| format!("JSONを保存できません: {error}"))?;
    file.sync_all()
        .map_err(|error| format!("JSONを確定できません: {error}"))?;
    replace_file(&temporary, path).map_err(|error| format!("JSONを置き換えられません: {error}"))
}

#[cfg(windows)]
fn replace_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::{iter::once, os::windows::ffi::OsStrExt};
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };
    let source: Vec<u16> = source.as_os_str().encode_wide().chain(once(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(once(0))
        .collect();
    // SAFETY: both pointers reference NUL-terminated UTF-16 buffers for this call.
    let succeeded = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if succeeded == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(windows))]
fn replace_file(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::rename(source, destination)
}

fn save_serializable<T: Serialize>(value: &T, path: &Path) -> Result<(), String> {
    let json = serde_json::to_value(value).map_err(|error| error.to_string())?;
    atomic_write_json(path, &json)
}

fn read_json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|error| format!("JSONを読み込めません: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("JSONの形式が不正です: {error}"))
}

fn validate_project(project: &Value) -> Result<(), String> {
    if project.get("schema_version").and_then(Value::as_u64) != Some(SCHEMA_VERSION.into())
        || project.get("kind").and_then(Value::as_str) != Some(PROJECT_KIND)
        || !project.get("subtitles").is_some_and(Value::is_array)
    {
        return Err("このアプリで保存した対応済みJSONを選択してください。".into());
    }
    Ok(())
}

fn save_legacy_request(
    request: &LegacyAnalysisRequest,
    directory: &Path,
) -> Result<PathBuf, String> {
    request.validate()?;
    fs::create_dir_all(directory).map_err(|error| format!("保存先を作成できません: {error}"))?;
    let path = directory.join(format!("{}.json", unique_name("request")?));
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|error| format!("JSONファイルを作成できません: {error}"))?;
    serde_json::to_writer_pretty(file, request)
        .map_err(|error| format!("JSONファイルを保存できません: {error}"))?;
    Ok(path)
}

fn resolve_python(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let bundled = app
        .path()
        .resolve(
            format!("analyzer-runtime/{PYTHON_NAME}"),
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|error| error.to_string())?;
    if bundled.is_file() {
        return Ok(bundled);
    }
    if let Some(configured) = std::env::var_os("LOL_TRANSLATOR_PYTHON") {
        let configured = PathBuf::from(configured);
        if configured.is_absolute() && configured.is_file() {
            return Ok(configured);
        }
        return Err("LOL_TRANSLATOR_PYTHONにはPaddleOCRを導入したPython実行ファイルの絶対パスを指定してください。".into());
    }
    #[cfg(debug_assertions)]
    {
        for environment in [".venv", "ocr_evaluation/.venv-paddle"] {
            let development = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../analyzer")
                .join(environment)
                .join(VENV_BIN_DIRECTORY)
                .join(PYTHON_NAME);
            if development.is_file() {
                return Ok(development);
            }
        }
    }
    Err("PaddleOCR用Pythonが見つかりません。analyzer/.venvを作成するか、LOL_TRANSLATOR_PYTHONを設定してください。".into())
}

fn resolve_script(app: &tauri::AppHandle, name: &str) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    {
        let development = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../analyzer")
            .join(name);
        if development.is_file() {
            return Ok(development);
        }
    }
    app.path()
        .resolve(
            format!("analyzer/{name}"),
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|error| error.to_string())
}

fn configure_command(command: &mut Command) {
    command.env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
}

fn spawn_job(
    executable: &Path,
    script: &Path,
    directory: PathBuf,
    arguments: &[String],
) -> Result<(String, AnalysisJob), String> {
    fs::create_dir_all(&directory)
        .map_err(|error| format!("ジョブ保存先を作成できません: {error}"))?;
    let progress_path = directory.join("progress.json");
    let result_path = directory.join("result.json");
    let cancel_path = directory.join("cancel.requested");
    let stdout = File::create(directory.join("stdout.log")).map_err(|error| error.to_string())?;
    let stderr_path = directory.join("stderr.log");
    let stderr = File::create(&stderr_path).map_err(|error| error.to_string())?;
    let mut command = Command::new(executable);
    command
        .arg(script)
        .args(arguments)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    configure_command(&mut command);
    let child = command
        .spawn()
        .map_err(|error| format!("Python解析プロセスを起動できません: {error}"))?;
    let id = directory
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| "ジョブIDを作成できません。".to_string())?
        .to_owned();
    Ok((
        id,
        AnalysisJob {
            child,
            progress_path,
            result_path,
            cancel_path,
            stderr_path,
        },
    ))
}

fn jobs_directory(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    Ok(app
        .path()
        .home_dir()
        .map_err(|error| error.to_string())?
        .join(".lol-translator")
        .join("jobs"))
}

fn insert_job(state: &AnalyzerState, id: String, job: AnalysisJob) -> Result<StartedJob, String> {
    state
        .jobs
        .lock()
        .map_err(|_| "解析ジョブの状態を更新できません。".to_string())?
        .insert(id.clone(), job);
    Ok(StartedJob { job_id: id })
}

#[tauri::command]
pub async fn start_analysis(
    app: tauri::AppHandle,
    state: State<'_, AnalyzerState>,
    request: RuntimeAnalysisRequest,
) -> Result<StartedJob, String> {
    request.validate()?;
    let executable = resolve_python(&app)?;
    let script = resolve_script(&app, "runtime_cli.py")?;
    let id = unique_name("analysis")?;
    let directory = jobs_directory(&app)?.join(&id);
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let request_path = directory.join("request.json");
    save_serializable(&request, &request_path)?;
    let arguments = vec![
        "analyze".into(),
        "--request".into(),
        request_path.to_string_lossy().into_owned(),
        "--progress".into(),
        directory
            .join("progress.json")
            .to_string_lossy()
            .into_owned(),
        "--result".into(),
        directory.join("result.json").to_string_lossy().into_owned(),
        "--cancel".into(),
        directory
            .join("cancel.requested")
            .to_string_lossy()
            .into_owned(),
        "--work-dir".into(),
        directory.join("work").to_string_lossy().into_owned(),
    ];
    let (job_id, job) = spawn_job(&executable, &script, directory, &arguments)?;
    insert_job(&state, job_id, job)
}

#[tauri::command]
pub async fn start_retranslation(
    app: tauri::AppHandle,
    state: State<'_, AnalyzerState>,
    project: Value,
) -> Result<StartedJob, String> {
    validate_project(&project)?;
    let executable = resolve_python(&app)?;
    let script = resolve_script(&app, "runtime_cli.py")?;
    let id = unique_name("retranslation")?;
    let directory = jobs_directory(&app)?.join(&id);
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let project_path = directory.join("project.json");
    atomic_write_json(&project_path, &project)?;
    let arguments = vec![
        "retranslate".into(),
        "--project".into(),
        project_path.to_string_lossy().into_owned(),
        "--progress".into(),
        directory
            .join("progress.json")
            .to_string_lossy()
            .into_owned(),
        "--result".into(),
        directory.join("result.json").to_string_lossy().into_owned(),
        "--cancel".into(),
        directory
            .join("cancel.requested")
            .to_string_lossy()
            .into_owned(),
    ];
    let (job_id, job) = spawn_job(&executable, &script, directory, &arguments)?;
    insert_job(&state, job_id, job)
}

#[tauri::command]
pub fn analysis_job_status(
    state: State<'_, AnalyzerState>,
    job_id: String,
) -> Result<JobSnapshot, String> {
    let mut jobs = state
        .jobs
        .lock()
        .map_err(|_| "解析ジョブの状態を確認できません。".to_string())?;
    let job = jobs
        .get_mut(&job_id)
        .ok_or_else(|| "解析ジョブが見つかりません。".to_string())?;
    let exit = job.child.try_wait().map_err(|error| error.to_string())?;
    let progress = if job.progress_path.is_file() {
        read_json(&job.progress_path)?
    } else if let Some(status) = exit {
        let detail = fs::read_to_string(&job.stderr_path).unwrap_or_default();
        json!({"state":"failed","phase":"failed","current":0,"total":0,"message":"解析プロセスが終了しました","error":format!("Pythonが{status}で終了しました。{}", detail.trim())})
    } else {
        json!({"state":"running","phase":"starting","current":0,"total":0,"message":"Python解析プロセスを起動中","error":null})
    };
    let project = if progress.get("state").and_then(Value::as_str) == Some("completed")
        && job.result_path.is_file()
    {
        Some(read_json(&job.result_path)?)
    } else {
        None
    };
    Ok(JobSnapshot {
        job_id,
        progress,
        project,
    })
}

#[tauri::command]
pub fn cancel_analysis_job(state: State<'_, AnalyzerState>, job_id: String) -> Result<(), String> {
    let mut jobs = state
        .jobs
        .lock()
        .map_err(|_| "解析ジョブをキャンセルできません。".to_string())?;
    let job = jobs
        .get_mut(&job_id)
        .ok_or_else(|| "解析ジョブが見つかりません。".to_string())?;
    File::create(&job.cancel_path).map_err(|error| error.to_string())?;
    for _ in 0..40 {
        if job
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    terminate_process_tree(&mut job.child)?;
    atomic_write_json(
        &job.progress_path,
        &json!({"state":"cancelled","phase":"cancelled","current":0,"total":0,"message":"解析をキャンセルしました","error":null}),
    )
}

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    let status = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .creation_flags(0x08000000)
        .status()
        .map_err(|error| format!("解析プロセスを終了できません: {error}"))?;
    if !status.success()
        && child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
    {
        return Err("解析プロセスツリーを終了できません。".into());
    }
    child.wait().map_err(|error| error.to_string())?;
    Ok(())
}

#[cfg(not(windows))]
fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
    child.kill().map_err(|error| error.to_string())?;
    child.wait().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
pub async fn save_analysis_project(path: PathBuf, project: Value) -> Result<(), String> {
    validate_project(&project)?;
    if !path.is_absolute() {
        return Err("保存先に絶対パスを指定してください。".into());
    }
    tauri::async_runtime::spawn_blocking(move || atomic_write_json(&path, &project))
        .await
        .map_err(|error| format!("保存処理が中断しました: {error}"))?
}

#[tauri::command]
pub async fn load_analysis_project(path: PathBuf) -> Result<Value, String> {
    if !path.is_absolute() || !path.is_file() {
        return Err("読み込むJSONファイルが見つかりません。".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let project = read_json(&path)?;
        validate_project(&project)?;
        Ok(project)
    })
    .await
    .map_err(|error| format!("読込処理が中断しました: {error}"))?
}

#[tauri::command]
pub async fn validate_analysis_request(
    app: tauri::AppHandle,
    request: LegacyAnalysisRequest,
) -> Result<String, String> {
    let directory = app
        .path()
        .home_dir()
        .map_err(|error| error.to_string())?
        .join(".lol-translator")
        .join("requests");
    tauri::async_runtime::spawn_blocking(move || {
        let path = save_legacy_request(&request, &directory)?;
        Ok(path.to_string_lossy().into_owned())
    })
    .await
    .map_err(|error| format!("検証処理が中断しました: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> RuntimeAnalysisRequest {
        RuntimeAnalysisRequest {
            schema_version: 1,
            video_path: std::env::current_exe().unwrap(),
            subtitle_region: Region {
                x: 0.1,
                y: 0.7,
                width: 0.8,
                height: 0.2,
            },
            analysis_range: AnalysisRange {
                mode: RangeMode::Range,
                start_seconds: Some(60.0),
                end_seconds: Some(960.0),
            },
            settings: AnalysisSettings {
                sample_interval_ms: 200,
                line_split_ratio: None,
            },
        }
    }

    #[test]
    fn accepts_long_ranges_and_whole_video_without_a_three_minute_limit() {
        assert!(request().validate().is_ok());
        let mut whole = request();
        whole.analysis_range = AnalysisRange {
            mode: RangeMode::Whole,
            start_seconds: None,
            end_seconds: None,
        };
        assert!(whole.validate().is_ok());
    }

    #[test]
    fn rejects_invalid_region_range_and_settings() {
        let mut value = request();
        value.subtitle_region.width = 0.0;
        assert!(value.validate().is_err());
        value = request();
        value.analysis_range.end_seconds = Some(30.0);
        assert!(value.validate().is_err());
        value = request();
        value.settings.sample_interval_ms = 49;
        assert!(value.validate().is_err());
        value = request();
        value.settings.line_split_ratio = Some(0.95);
        assert!(value.validate().is_err());
    }

    #[test]
    fn saves_unicode_project_atomically_and_loads_it() {
        let directory = std::env::temp_dir().join(unique_name("lol-translator-rust-test").unwrap());
        let path = directory.join("結果.json");
        let project =
            json!({"schema_version":1,"kind":PROJECT_KIND,"subtitles":[],"note":"한국어と日本語"});
        validate_project(&project).unwrap();
        atomic_write_json(&path, &project).unwrap();
        assert_eq!(read_json(&path).unwrap(), project);
        let updated =
            json!({"schema_version":1,"kind":PROJECT_KIND,"subtitles":[],"note":"修正済み"});
        atomic_write_json(&path, &updated).unwrap();
        assert_eq!(read_json(&path).unwrap(), updated);
        assert!(!path.with_extension("json.tmp").exists());
        fs::remove_file(path).unwrap();
        fs::remove_dir(directory).unwrap();
    }

    #[test]
    fn legacy_request_still_preserves_the_existing_contract() {
        let legacy = LegacyAnalysisRequest {
            video_path: std::env::current_exe().unwrap(),
            subtitle_region: Region {
                x: 0.1,
                y: 0.7,
                width: 0.8,
                height: 0.2,
            },
        };
        assert!(legacy.validate().is_ok());
    }
}
