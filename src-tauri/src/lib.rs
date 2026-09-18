mod analyzer;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(analyzer::AnalyzerState::default())
        .invoke_handler(tauri::generate_handler![
            analyzer::validate_analysis_request,
            analyzer::start_analysis,
            analyzer::start_retranslation,
            analyzer::analysis_job_status,
            analyzer::cancel_analysis_job,
            analyzer::save_analysis_project,
            analyzer::load_analysis_project,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
