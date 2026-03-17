//! OAuth2 PKCE authentication for Spotify Web API.

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const REDIRECT_URI: &str = "http://127.0.0.1:8888/callback";
const SCOPES: &str = "user-read-currently-playing user-read-playback-state user-modify-playback-state";
const TOKEN_URL: &str = "https://accounts.spotify.com/api/token";
const AUTH_URL: &str = "https://accounts.spotify.com/authorize";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct TokenData {
    access_token: Option<String>,
    refresh_token: Option<String>,
    expires_at: f64,
}

pub struct SpotifyAuth {
    client_id: String,
    tokens: Mutex<TokenData>,
}

fn token_dir() -> PathBuf {
    // Match Python version: always use ~/.config/spottt/
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".config")
        .join("spottt")
}

fn token_file() -> PathBuf {
    token_dir().join("tokens.json")
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64()
}

fn random_urlsafe(len: usize) -> String {
    let mut rng = rand::thread_rng();
    let bytes: Vec<u8> = (0..len).map(|_| rng.gen()).collect();
    URL_SAFE_NO_PAD.encode(&bytes)
}

impl SpotifyAuth {
    pub fn new(client_id: String) -> Self {
        let mut tokens = TokenData::default();
        // Load existing tokens
        if let Ok(data) = std::fs::read_to_string(token_file()) {
            if let Ok(t) = serde_json::from_str::<TokenData>(&data) {
                tokens = t;
            }
        }
        Self {
            client_id,
            tokens: Mutex::new(tokens),
        }
    }

    fn save_tokens(&self, tokens: &TokenData) {
        let dir = token_dir();
        let _ = std::fs::create_dir_all(&dir);
        let path = token_file();
        let _ = std::fs::write(&path, serde_json::to_string_pretty(tokens).unwrap());
        // Restrict token file to owner-only (0600)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
        }
    }

    pub fn get_token(&self) -> Result<String, String> {
        let tokens = self.tokens.lock().unwrap().clone();

        // Valid token?
        if let Some(ref at) = tokens.access_token {
            if now_secs() < tokens.expires_at - 60.0 {
                return Ok(at.clone());
            }
        }

        // Try refresh
        if let Some(ref rt) = tokens.refresh_token {
            match self.refresh(rt) {
                Ok(at) => return Ok(at),
                Err(_) => {} // Fall through to full auth
            }
        }

        // Full authorization
        self.authorize()
    }

    pub fn invalidate_access_token(&self) {
        let mut tokens = self.tokens.lock().unwrap();
        tokens.access_token = None;
    }

    fn generate_pkce() -> (String, String) {
        let verifier = random_urlsafe(64);
        let verifier = &verifier[..verifier.len().min(128)];
        let hash = Sha256::digest(verifier.as_bytes());
        let challenge = URL_SAFE_NO_PAD.encode(hash);
        (verifier.to_string(), challenge)
    }

    fn authorize(&self) -> Result<String, String> {
        let (verifier, challenge) = Self::generate_pkce();
        let state = random_urlsafe(16);

        let params = [
            ("client_id", self.client_id.as_str()),
            ("response_type", "code"),
            ("redirect_uri", REDIRECT_URI),
            ("code_challenge_method", "S256"),
            ("code_challenge", &challenge),
            ("scope", SCOPES),
            ("state", &state),
        ];

        let query = params
            .iter()
            .map(|(k, v)| format!("{}={}", k, urlencoding(v)))
            .collect::<Vec<_>>()
            .join("&");

        let auth_url = format!("{}?{}", AUTH_URL, query);

        // Start callback server — try ports 8888, 8889, 8890
        let server = Self::start_callback_server()?;

        eprintln!("\n  Opening browser for Spotify authorization...");
        let _ = open::that(&auth_url);

        // Wait for callback (300s timeout)
        let code = Self::wait_for_callback(server, &state)?;

        self.exchange_code(&code, &verifier)
    }

    fn start_callback_server() -> Result<tiny_http::Server, String> {
        for port in [8888, 8889, 8890] {
            let addr = format!("127.0.0.1:{}", port);
            if let Ok(server) = tiny_http::Server::http(&addr) {
                return Ok(server);
            }
        }
        Err("Could not bind callback server on ports 8888-8890".into())
    }

    fn wait_for_callback(server: tiny_http::Server, expected_state: &str) -> Result<String, String> {
        let timeout = std::time::Duration::from_secs(300);
        let start = std::time::Instant::now();

        loop {
            if start.elapsed() > timeout {
                return Err("Authorization timed out after 300 seconds".into());
            }

            let request = match server.recv_timeout(std::time::Duration::from_secs(1)) {
                Ok(Some(r)) => r,
                Ok(None) => continue,
                Err(e) => return Err(format!("Server error: {}", e)),
            };

            let url = request.url().to_string();
            if !url.starts_with("/callback") {
                let _ = request.respond(tiny_http::Response::from_string("ok"));
                continue;
            }

            // Parse code and state from query
            let mut code = None;
            let mut returned_state = None;
            if let Some(query) = url.split('?').nth(1) {
                for pair in query.split('&') {
                    let mut kv = pair.splitn(2, '=');
                    if let (Some(k), Some(v)) = (kv.next(), kv.next()) {
                        match k {
                            "code" => code = Some(v.to_string()),
                            "state" => returned_state = Some(v.to_string()),
                            _ => {}
                        }
                    }
                }
            }

            // Validate state to prevent CSRF
            if returned_state.as_deref() != Some(expected_state) {
                let _ = request.respond(tiny_http::Response::from_string("State mismatch"));
                return Err("OAuth state mismatch — possible CSRF".into());
            }

            let html = if code.is_some() {
                "<html><body style='background:#121212;color:#1db954;\
                 font-family:monospace;text-align:center;padding:80px'>\
                 <h1>Spottt Authorized</h1>\
                 <p>You can close this tab.</p></body></html>"
            } else {
                "<html><body>Authorization failed</body></html>"
            };

            let response = tiny_http::Response::from_string(html)
                .with_header(
                    "Content-Type: text/html"
                        .parse::<tiny_http::Header>()
                        .unwrap(),
                );
            let _ = request.respond(response);

            if let Some(c) = code {
                return Ok(c);
            }
        }
    }

    fn exchange_code(&self, code: &str, verifier: &str) -> Result<String, String> {
        let body = format!(
            "grant_type=authorization_code&code={}&redirect_uri={}&client_id={}&code_verifier={}",
            urlencoding(code),
            urlencoding(REDIRECT_URI),
            urlencoding(&self.client_id),
            urlencoding(verifier),
        );

        let resp = reqwest::blocking::Client::new()
            .post(TOKEN_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body(body)
            .send()
            .map_err(|e| format!("Token exchange failed: {}", e))?;

        let result: serde_json::Value = resp
            .json()
            .map_err(|e| format!("Token parse failed: {}", e))?;

        let access_token = result["access_token"]
            .as_str()
            .ok_or("No access_token in response")?
            .to_string();
        let refresh_token = result["refresh_token"].as_str().map(String::from);
        let expires_in = result["expires_in"].as_f64().unwrap_or(3600.0);

        let mut tokens = self.tokens.lock().unwrap();
        tokens.access_token = Some(access_token.clone());
        if let Some(rt) = refresh_token {
            tokens.refresh_token = Some(rt);
        }
        tokens.expires_at = now_secs() + expires_in;
        self.save_tokens(&tokens);

        Ok(access_token)
    }

    fn refresh(&self, refresh_token: &str) -> Result<String, String> {
        let body = format!(
            "grant_type=refresh_token&refresh_token={}&client_id={}",
            urlencoding(refresh_token),
            urlencoding(&self.client_id),
        );

        let resp = reqwest::blocking::Client::new()
            .post(TOKEN_URL)
            .header("Content-Type", "application/x-www-form-urlencoded")
            .body(body)
            .send()
            .map_err(|e| format!("Refresh failed: {}", e))?;

        let result: serde_json::Value = resp
            .json()
            .map_err(|e| format!("Refresh parse failed: {}", e))?;

        if result.get("error").is_some() {
            return Err(format!("Refresh error: {}", result));
        }

        let access_token = result["access_token"]
            .as_str()
            .ok_or("No access_token in refresh response")?
            .to_string();
        let new_refresh = result["refresh_token"].as_str().map(String::from);
        let expires_in = result["expires_in"].as_f64().unwrap_or(3600.0);

        let mut tokens = self.tokens.lock().unwrap();
        tokens.access_token = Some(access_token.clone());
        if let Some(rt) = new_refresh {
            tokens.refresh_token = Some(rt);
        }
        tokens.expires_at = now_secs() + expires_in;
        self.save_tokens(&tokens);

        Ok(access_token)
    }
}

fn urlencoding(s: &str) -> String {
    let mut result = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                result.push(b as char);
            }
            _ => {
                result.push_str(&format!("%{:02X}", b));
            }
        }
    }
    result
}
