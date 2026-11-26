mod analyzer;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(all(windows, feature = "windows-e2e"))]
    let builder = match std::env::var("TAURI_WEBDRIVER_PORT")
        .ok()
        .and_then(|port| port.parse::<u16>().ok())
        .filter(|&port| port != 0)
    {
        Some(port) => builder.plugin(tauri_plugin_wdio_webdriver::init_with_port(port)),
        None => builder,
    };

    builder
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
