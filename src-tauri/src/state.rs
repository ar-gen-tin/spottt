//! Shared application state + payload structs.

use crate::renderer::AsciiRenderer;
use serde::Serialize;
use std::sync::Mutex;

#[derive(Debug, Clone, Serialize)]
pub struct StatePayload {
    pub track_id: Option<String>,
    pub name: String,
    pub artist: String,
    pub album: String,
    pub progress_ms: u64,
    pub duration_ms: u64,
    pub is_playing: bool,
    pub bpm: f64,
    pub cover_url: Option<String>,
    pub error: Option<String>,
}

impl Default for StatePayload {
    fn default() -> Self {
        Self {
            track_id: None,
            name: String::new(),
            artist: String::new(),
            album: String::new(),
            progress_ms: 0,
            duration_ms: 0,
            is_playing: false,
            bpm: 120.0,
            cover_url: None,
            error: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ArtPayload {
    pub html: String,
    pub style: String,
}

pub struct AppState {
    pub state: Mutex<StatePayload>,
    pub renderer: Mutex<AsciiRenderer>,
    pub image_bytes: Mutex<Option<Vec<u8>>>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(StatePayload::default()),
            renderer: Mutex::new(AsciiRenderer::new()),
            image_bytes: Mutex::new(None),
        }
    }
}
