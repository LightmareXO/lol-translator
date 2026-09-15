use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::Manager;

#[cfg(windows)]
const PYTHON_NAME: &str = "python.exe";
#[cfg(windows)]
const VENV_BIN_DIRECTORY: &str = "Scripts";
#[cfg(not(windows))]
const PYTHON_NAME: &str = "python";
#[cfg(not(windows))]
const VENV_BIN_DIRECTORY: &str = "bin";

#[derive(Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Region {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

#[derive(Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AnalysisRequest {
    video_path: PathBuf,
    subtitle_region: Region,
}

impl AnalysisRequest {
    fn validate(&self) -> Result<(), String> {
        if !self.video_path.is_absolute() || !self.video_path.is_file() {
            return Err("動画のパスが不正か、ファイルが存在しません。".into());
        }
        let r = &self.subtitle_region;
        if [r.x, r.y, r.width, r.height]
            .iter()
            .any(|v| !v.is_finite() || !(0.0..=1.0).contains(v))
            || r.width <= 0.0
            || r.height <= 0.0
            || r.x + r.width > 1.0 + 1e-9
            || r.y + r.height > 1.0 + 1e-9
        {
            return Err("字幕範囲は映像内の、面積がある正規化座標で指定してください。".into());
        }
        Ok(())
    }
}

fn save_request(request: &AnalysisRequest, directory: &Path) -> Result<PathBuf, String> {
    request.validate()?;
    fs::create_dir_all(directory).map_err(|e| format!("保存先を作成できません: {e}"))?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let path = directory.join(format!("request-{}-{stamp}.json", std::process::id()));
    // Never overwrite an earlier request, even if the clock returns the same time.
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .map_err(|e| format!("JSONファイルを作成できません: {e}"))?;
    serde_json::to_writer_pretty(file, request)
        .map_err(|e| format!("JSONファイルを保存できません: {e}"))?;
    Ok(path)
}

fn run_python(
    executable: &std::ffi::OsStr,
    script: &Path,
    path: &Path,
) -> Result<AnalysisRequest, String> {
    let mut command = Command::new(executable);
    command
        .arg(script)
        .arg(path)
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let output = command.output().map_err(|e| {
        format!("Pythonを起動できません。Python 3.10以上と実行パスを確認してください: {e}")
    })?;
    if !output.status.success() {
        return Err(format!(
            "Pythonの入力検証に失敗しました: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("Pythonから正しい検証結果を受け取れませんでした: {e}"))
}

fn resolve_python(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let bundled = app
        .path()
        .resolve(
            format!("analyzer-runtime/{PYTHON_NAME}"),
            tauri::path::BaseDirectory::Resource,
        )
        .map_err(|e| e.to_string())?;
    if bundled.is_file() {
        return Ok(bundled);
    }

    if let Some(configured) = std::env::var_os("LOL_TRANSLATOR_PYTHON") {
        let configured = PathBuf::from(configured);
        if configured.is_absolute() && configured.is_file() {
            return Ok(configured);
        }
        return Err(
            "LOL_TRANSLATOR_PYTHONには存在するPython実行ファイルの絶対パスを指定してください。"
                .into(),
        );
    }

    #[cfg(debug_assertions)]
    {
        let development = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../analyzer/.venv")
            .join(VENV_BIN_DIRECTORY)
            .join(PYTHON_NAME);
        if development.is_file() {
            return Ok(development);
        }
    }

    Err("専用Pythonランタイムが見つかりません。開発時はanalyzer/.venvを作成してください。".into())
}

#[tauri::command]
pub async fn validate_analysis_request(
    app: tauri::AppHandle,
    request: AnalysisRequest,
) -> Result<String, String> {
    // Microsoft Store Python redirects AppData paths into its package-local
    // storage. Keeping the hand-off file below the user's home directory makes
    // the same path visible to packaged and conventional Python installations.
    let directory = app
        .path()
        .home_dir()
        .map_err(|e| e.to_string())?
        .join(".lol-translator")
        .join("requests");
    let script = app
        .path()
        .resolve("analyzer/main.py", tauri::path::BaseDirectory::Resource)
        .map_err(|e| e.to_string())?;
    let executable = resolve_python(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let path = save_request(&request, &directory)?;
        let validated = run_python(executable.as_os_str(), &script, &path)?;
        if validated != request {
            return Err("Pythonの検証結果が送信した内容と一致しません。".into());
        }
        Ok(path.to_string_lossy().into_owned())
    })
    .await
    .map_err(|e| format!("検証処理が中断しました: {e}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> AnalysisRequest {
        AnalysisRequest {
            video_path: std::env::current_exe().unwrap(),
            subtitle_region: Region {
                x: 0.1,
                y: 0.7,
                width: 0.8,
                height: 0.2,
            },
        }
    }

    #[test]
    fn validates_bounds_and_existing_file() {
        let mut r = request();
        assert!(r.validate().is_ok());
        for value in [0.0, -1.0, 1.0, f64::NAN, f64::INFINITY] {
            r.subtitle_region.width = value;
            assert!(r.validate().is_err());
        }
        r = request();
        r.video_path = PathBuf::from("missing.mp4");
        assert!(r.validate().is_err());
        r.video_path = std::env::temp_dir();
        assert!(r.validate().is_err());
    }

    #[test]
    fn saves_exact_contract_without_overwriting() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "lol-translator-test-{}-{stamp}",
            std::process::id()
        ));
        let r = request();
        let first = save_request(&r, &directory).unwrap();
        let second = save_request(&r, &directory).unwrap();
        assert_ne!(first, second);
        let saved: AnalysisRequest = serde_json::from_slice(&fs::read(&first).unwrap()).unwrap();
        assert_eq!(saved, r);
        fs::remove_file(first).unwrap();
        fs::remove_file(second).unwrap();
        fs::remove_dir(directory).unwrap();
    }

    #[test]
    fn reports_missing_python() {
        let error = run_python(
            std::ffi::OsStr::new("lol-translator-nonexistent-python"),
            Path::new("main.py"),
            Path::new("request.json"),
        )
        .unwrap_err();
        assert!(error.contains("Pythonを起動できません"));
    }

    #[test]
    #[ignore = "Requires Python 3.10+ on PATH or LOL_TRANSLATOR_PYTHON"]
    fn python_round_trip() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "lol-translator-python-{}-{stamp}",
            std::process::id()
        ));
        fs::create_dir(&directory).unwrap();
        let mut r = request();
        r.video_path = directory.join("韓国語 영상 [test].mp4");
        r.subtitle_region.x = 0.12345678901234568;
        fs::write(&r.video_path, b"existence validation only").unwrap();
        let path = save_request(&r, &directory).unwrap();
        let executable = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../analyzer/.venv")
            .join(VENV_BIN_DIRECTORY)
            .join(PYTHON_NAME);
        assert!(
            executable.is_file(),
            "Create analyzer/.venv before running this test"
        );
        let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("../analyzer/main.py");
        assert_eq!(
            run_python(executable.as_os_str(), &script, &path).unwrap(),
            r
        );
        fs::remove_file(&r.video_path).unwrap();
        assert!(run_python(executable.as_os_str(), &script, &path)
            .unwrap_err()
            .contains("Pythonの入力検証に失敗"));
        fs::remove_file(path).unwrap();
        fs::remove_dir(directory).unwrap();
    }
}
