//! Spottt — Spotify desktop widget (Tauri backend).
//! Shows album cover + spectrum visualizer. No ASCII art.

pub mod auth;
pub mod commands;
pub mod renderer;
pub mod spotify;
pub mod state;

use auth::SpotifyAuth;
use spotify::SpotifyClient;
use state::{AppState, StatePayload};

use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Duration;
use tauri::tray::TrayIconBuilder;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::Manager;

/// Lock a Mutex, recovering from poison if a thread panicked while holding it.
pub fn safe_lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

pub static ACTION_SENDER: OnceLock<Mutex<Option<std::sync::mpsc::Sender<String>>>> =
    OnceLock::new();

fn get_client_id() -> Result<String, String> {
    if let Ok(id) = std::env::var("SPOTIFY_CLIENT_ID")
        .or_else(|_| std::env::var("SPOTIPY_CLIENT_ID"))
    {
        if !id.is_empty() {
            return Ok(id);
        }
    }

    let home = dirs::home_dir().unwrap_or_default();
    for path in [
        home.join(".config").join("spottt").join("config.json"),
        dirs::config_dir()
            .unwrap_or_else(|| home.join(".config"))
            .join("spottt")
            .join("config.json"),
    ] {
        if let Ok(data) = std::fs::read_to_string(&path) {
            if let Ok(json) = serde_json::from_str::<serde_json::Value>(&data) {
                if let Some(id) = json["client_id"].as_str().filter(|s| !s.is_empty()) {
                    return Ok(id.to_string());
                }
            }
        }
    }

    Err("Set SPOTIFY_CLIENT_ID or add client_id to ~/.config/spottt/config.json".to_string())
}

fn spawn_poller(app_state: Arc<AppState>, client_id: String) {
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    ACTION_SENDER.get_or_init(|| Mutex::new(Some(tx)));

    std::thread::spawn(move || {
        let auth = Arc::new(SpotifyAuth::new(client_id));

        match auth.get_token() {
            Ok(_) => eprintln!("  Spotify authenticated"),
            Err(e) => {
                eprintln!("  Auth failed: {}", e);
                safe_lock(&app_state.state).error = Some(format!("Auth failed: {}", e));
                return;
            }
        }

        let client = SpotifyClient::new(auth);
        let mut bpm: f64 = 120.0;
        let mut consecutive_failures: u8 = 0;

        loop {
            // Process playback actions
            while let Ok(action) = rx.try_recv() {
                handle_action(&action, &client, &app_state);
            }

            match client.get_currently_playing() {
                Ok(Some(track)) => {
                    consecutive_failures = 0;
                    let track_changed = client.track_changed(&track);

                    // Get cover URL
                    let cover_url = track
                        .best_cover_url()
                        .map(|s| s.to_string())
                        .or_else(|| {
                            track
                                .artist_ids
                                .first()
                                .and_then(|id| client.get_artist_image_url(id))
                        });

                    if track_changed {
                        // Fetch BPM
                        if let Some(features) = client.get_audio_features(&track.track_id) {
                            bpm = features["tempo"].as_f64().unwrap_or(120.0);
                        }
                        // Download cover image for ASCII art rendering
                        if let Some(ref url) = cover_url {
                            if let Ok(bytes) = client.download_image(url) {
                                *safe_lock(&app_state.image_bytes) = Some(bytes);
                                safe_lock(&app_state.renderer).clear_cache();
                            }
                        }
                    }

                    let mut state = safe_lock(&app_state.state);
                    state.track_id = Some(track.track_id.clone());
                    state.name = track.name.clone();
                    state.artist = track.artist_display();
                    state.album = track.album.clone();
                    state.progress_ms = track.interpolated_progress_ms();
                    state.duration_ms = track.duration_ms;
                    state.is_playing = track.is_playing;
                    state.bpm = bpm;
                    state.cover_url = cover_url;
                    state.error = None;
                }
                Ok(None) => {
                    consecutive_failures = 0;
                    *safe_lock(&app_state.state) = StatePayload::default();
                }
                Err(e) => {
                    consecutive_failures += 1;
                    eprintln!("  Poll error ({}): {}", consecutive_failures, e);
                    if consecutive_failures >= 3 {
                        safe_lock(&app_state.state).error = Some("Connection lost".into());
                    }
                }
            }

            std::thread::sleep(Duration::from_secs(2));
        }
    });
}

fn handle_action(action: &str, client: &SpotifyClient, app_state: &AppState) {
    match action {
        "play_pause" => {
            let is_playing = safe_lock(&app_state.state).is_playing;
            if is_playing {
                let _ = client.put_action("/me/player/pause");
            } else {
                let _ = client.put_action("/me/player/play");
            }
        }
        "next_track" => { let _ = client.post_action("/me/player/next"); }
        "prev_track" => { let _ = client.post_action("/me/player/previous"); }
        "shuffle" => { let _ = client.put_action("/me/player/shuffle?state=true"); }
        "repeat" => { let _ = client.put_action("/me/player/repeat?state=context"); }
        _ => {}
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let client_id = match get_client_id() {
        Ok(id) => id,
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(1);
        }
    };

    let app_state = Arc::new(AppState::new());
    let poller_state = app_state.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(app_state)
        .setup(move |app| {
            spawn_poller(poller_state, client_id);

            // System tray
            let show = MenuItemBuilder::new("Show").id("show").build(app)?;
            let quit = MenuItemBuilder::new("Quit").id("quit").build(app)?;
            let menu = MenuBuilder::new(app).items(&[&show, &quit]).build()?;

            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "show" => {
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click { button: tauri::tray::MouseButton::Left, .. } = event {
                        if let Some(win) = tray.app_handle().get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_state,
            commands::do_action,
            commands::get_cover_art,
        ])
        .run(tauri::generate_context!())
        .expect("error while running spottt");
}
