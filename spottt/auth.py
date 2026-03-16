"""OAuth2 PKCE authentication for Spotify Web API."""

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser

TOKEN_DIR = os.path.expanduser("~/.config/spottt")
TOKEN_FILE = os.path.join(TOKEN_DIR, "tokens.json")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-currently-playing user-read-playback-state user-modify-playback-state"


class SpotifyAuth:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.access_token = None
        self.refresh_token = None
        self.expires_at = 0
        self._load_tokens()

    def _load_tokens(self):
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE) as f:
                    data = json.load(f)
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.expires_at = data.get("expires_at", 0)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_tokens(self):
        os.makedirs(TOKEN_DIR, exist_ok=True)
        os.chmod(TOKEN_DIR, 0o700)
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
            }, f, indent=2)
        os.chmod(TOKEN_FILE, 0o600)

    def get_token(self) -> str:
        if self.access_token and time.time() < self.expires_at - 60:
            return self.access_token
        if self.refresh_token:
            try:
                self._refresh()
                return self.access_token
            except Exception:
                pass
        self._authorize()
        return self.access_token

    def _generate_pkce(self):
        verifier = secrets.token_urlsafe(64)[:128]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return verifier, challenge

    def _authorize(self):
        verifier, challenge = self._generate_pkce()
        state = secrets.token_urlsafe(16)

        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        })

        auth_url = f"https://accounts.spotify.com/authorize?{params}"
        code_result = [None]

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                returned_state = params.get("state", [None])[0]
                if returned_state != state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch - possible CSRF attack")
                    return
                if "code" in params:
                    code_result[0] = params["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    html = (
                        "<html><body style='background:#121212;color:#1db954;"
                        "font-family:monospace;text-align:center;padding:80px'>"
                        "<h1>✓ Spottt Authorized</h1>"
                        "<p>You can close this tab and return to the terminal.</p>"
                        "</body></html>"
                    )
                    self.wfile.write(html.encode())
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Authorization failed")

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 8888), CallbackHandler)
        server.timeout = 300

        print("\n  Opening browser for Spotify authorization...")
        print(f"  If it doesn't open, visit:\n  {auth_url}\n")
        webbrowser.open(auth_url)

        import time as _time
        start = _time.time()
        while code_result[0] is None:
            if _time.time() - start > 300:
                server.server_close()
                raise RuntimeError("OAuth authorization timed out after 5 minutes")
            server.handle_request()

        server.server_close()
        self._exchange_code(code_result[0], verifier)

    def _token_request(self, data: dict):
        """POST to token endpoint, parse response, save tokens."""
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        self.access_token = result["access_token"]
        self.refresh_token = result.get("refresh_token", self.refresh_token)
        self.expires_at = time.time() + result["expires_in"]
        self._save_tokens()

    def _exchange_code(self, code: str, verifier: str):
        self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id,
            "code_verifier": verifier,
        })

    def _refresh(self):
        self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
        })

    def logout(self):
        self.access_token = None
        self.refresh_token = None
        self.expires_at = 0
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
