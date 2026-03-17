//! Tauri commands: get_state, do_action, get_cover_art.

use crate::safe_lock;
use crate::state::{AppState, ArtPayload, StatePayload};
use std::sync::Arc;
use tauri::State;

const ART_COLS: usize = 38;

#[tauri::command]
pub fn get_state(app_state: State<'_, Arc<AppState>>) -> StatePayload {
    safe_lock(&app_state.state).clone()
}

#[tauri::command]
pub fn do_action(action: String, _app_state: State<'_, Arc<AppState>>) {
    const ALLOWED: &[&str] = &["play_pause", "next_track", "prev_track", "shuffle", "repeat"];
    if !ALLOWED.contains(&action.as_str()) { return; }
    if let Some(sender) = crate::ACTION_SENDER.get() {
        if let Some(ref sender) = *safe_lock(sender) {
            let _ = sender.send(action);
        }
    }
}

/// Render cover art in a specific ASCII style. Called synchronously from frontend.
#[tauri::command]
pub fn get_cover_art(style: String, app_state: State<'_, Arc<AppState>>) -> ArtPayload {
    let image_bytes = safe_lock(&app_state.image_bytes);
    let Some(ref bytes) = *image_bytes else {
        return ArtPayload { html: String::new(), style };
    };

    let mut renderer = safe_lock(&app_state.renderer);

    // Set the renderer to the requested style
    let styles = crate::renderer::STYLES;
    for (i, s) in styles.iter().enumerate() {
        if *s == style {
            renderer.style_index = i;
            break;
        }
    }

    let track_id = safe_lock(&app_state.state)
        .track_id
        .clone()
        .unwrap_or_default();

    match renderer.render_to_html(bytes, ART_COLS, Some(&track_id)) {
        Ok(html) => ArtPayload { html, style },
        Err(_) => ArtPayload { html: String::new(), style },
    }
}
