//! Spotify Web API client — track info, images, polling.

use crate::auth::SpotifyAuth;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const API_BASE: &str = "https://api.spotify.com/v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrackInfo {
    pub track_id: String,
    pub name: String,
    pub artists: Vec<String>,
    pub album: String,
    pub album_id: String,
    pub album_images: Vec<AlbumImage>,
    pub artist_ids: Vec<String>,
    pub duration_ms: u64,
    pub progress_ms: u64,
    pub is_playing: bool,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlbumImage {
    pub url: String,
    pub height: Option<u32>,
    pub width: Option<u32>,
}

impl TrackInfo {
    pub fn artist_display(&self) -> String {
        self.artists.join(", ")
    }

    pub fn interpolated_progress_ms(&self) -> u64 {
        if !self.is_playing || self.timestamp == 0.0 {
            return self.progress_ms;
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let elapsed_ms = ((now - self.timestamp) * 1000.0) as u64;
        (self.progress_ms + elapsed_ms).min(self.duration_ms)
    }

    pub fn best_cover_url(&self) -> Option<&str> {
        if self.album_images.is_empty() {
            return None;
        }
        self.album_images
            .iter()
            .min_by_key(|img| {
                let h = img.height.unwrap_or(640) as i32;
                (h - 300).unsigned_abs()
            })
            .map(|img| img.url.as_str())
    }
}

pub struct SpotifyClient {
    auth: std::sync::Arc<SpotifyAuth>,
    last_track_id: Mutex<Option<String>>,
    artist_image_cache: Mutex<HashMap<String, Option<String>>>,
    http: reqwest::blocking::Client,
}

impl SpotifyClient {
    pub fn new(auth: std::sync::Arc<SpotifyAuth>) -> Self {
        Self {
            auth,
            last_track_id: Mutex::new(None),
            artist_image_cache: Mutex::new(HashMap::new()),
            http: reqwest::blocking::Client::builder()
                .timeout(std::time::Duration::from_secs(10))
                .build()
                .unwrap(),
        }
    }

    fn api_get(&self, path: &str) -> Result<Option<serde_json::Value>, String> {
        self.api_get_inner(path, 0)
    }

    fn api_get_inner(&self, path: &str, retry: u8) -> Result<Option<serde_json::Value>, String> {
        if retry > 2 {
            return Err("Too many retries".into());
        }

        let token = self.auth.get_token()?;
        let url = format!("{}{}", API_BASE, path);

        let resp = self
            .http
            .get(&url)
            .header("Authorization", format!("Bearer {}", token))
            .send()
            .map_err(|e| format!("API request failed: {}", e))?;

        let status = resp.status().as_u16();
        match status {
            204 => Ok(None),
            200 => {
                let body: serde_json::Value = resp
                    .json()
                    .map_err(|e| format!("JSON parse failed: {}", e))?;
                Ok(Some(body))
            }
            429 => {
                let retry_after: u64 = resp
                    .headers()
                    .get("Retry-After")
                    .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
                    .and_then(|v: &str| v.parse().ok())
                    .unwrap_or(5);
                std::thread::sleep(std::time::Duration::from_secs(retry_after));
                self.api_get_inner(path, retry + 1)
            }
            401 => {
                self.auth.invalidate_access_token();
                self.api_get_inner(path, retry + 1)
            }
            _ => Err(format!("API error {}: {}", status, resp.text().unwrap_or_default())),
        }
    }

    pub fn get_currently_playing(&self) -> Result<Option<TrackInfo>, String> {
        let data = self.api_get("/me/player/currently-playing")?;
        let data = match data {
            Some(d) => d,
            None => return Ok(None),
        };

        if data.get("item").is_none() || data["item"].is_null() {
            return Ok(None);
        }
        if data.get("currently_playing_type").and_then(|v| v.as_str()) != Some("track") {
            return Ok(None);
        }

        let item = &data["item"];
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        let artists: Vec<String> = item["artists"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|a| a["name"].as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        let artist_ids: Vec<String> = item["artists"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|a| a["id"].as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        let album_images: Vec<AlbumImage> = item["album"]["images"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|img| {
                        Some(AlbumImage {
                            url: img["url"].as_str()?.to_string(),
                            height: img["height"].as_u64().map(|h| h as u32),
                            width: img["width"].as_u64().map(|w| w as u32),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        Ok(Some(TrackInfo {
            track_id: item["id"].as_str().unwrap_or("").to_string(),
            name: item["name"].as_str().unwrap_or("").to_string(),
            artists,
            album: item["album"]["name"].as_str().unwrap_or("").to_string(),
            album_id: item["album"]["id"].as_str().unwrap_or("").to_string(),
            album_images,
            artist_ids,
            duration_ms: item["duration_ms"].as_u64().unwrap_or(0),
            progress_ms: data["progress_ms"].as_u64().unwrap_or(0),
            is_playing: data["is_playing"].as_bool().unwrap_or(false),
            timestamp: now,
        }))
    }

    pub fn get_artist_image_url(&self, artist_id: &str) -> Option<String> {
        {
            let cache = self.artist_image_cache.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(cached) = cache.get(artist_id) {
                return cached.clone();
            }
        }

        let data = self.api_get(&format!("/artists/{}", artist_id)).ok()?;
        let data = data?;

        let url = data["images"]
            .as_array()
            .and_then(|imgs| {
                imgs.iter()
                    .min_by_key(|img| {
                        let h = img["height"].as_u64().unwrap_or(640) as i32;
                        (h - 300).unsigned_abs()
                    })
                    .and_then(|img| img["url"].as_str().map(|s| s.to_string()))
            });

        self.artist_image_cache
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(artist_id.to_string(), url.clone());
        url
    }

    pub fn get_audio_features(&self, track_id: &str) -> Option<serde_json::Value> {
        self.api_get(&format!("/audio-features/{}", track_id))
            .ok()
            .flatten()
    }

    pub fn download_image(&self, url: &str) -> Result<Vec<u8>, String> {
        let resp = self
            .http
            .get(url)
            .send()
            .map_err(|e| format!("Image download failed: {}", e))?;
        let bytes = resp
            .bytes()
            .map_err(|e| format!("Image read failed: {}", e))?;
        Ok(bytes.to_vec())
    }

    pub fn track_changed(&self, track: &TrackInfo) -> bool {
        let mut last = self.last_track_id.lock().unwrap_or_else(|e| e.into_inner());
        let changed = last.as_deref() != Some(&track.track_id);
        *last = Some(track.track_id.clone());
        changed
    }

    pub fn put_action(&self, endpoint: &str) -> Result<(), String> {
        let token = self.auth.get_token()?;
        let url = format!("{}{}", API_BASE, endpoint);
        let _resp = self
            .http
            .put(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Length", "0")
            .send()
            .map_err(|e| format!("Action failed: {}", e))?;
        Ok(())
    }

    pub fn post_action(&self, endpoint: &str) -> Result<(), String> {
        let token = self.auth.get_token()?;
        let url = format!("{}{}", API_BASE, endpoint);
        let _resp = self
            .http
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .header("Content-Length", "0")
            .send()
            .map_err(|e| format!("Action failed: {}", e))?;
        Ok(())
    }
}
