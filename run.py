#!/usr/bin/env python3
"""Spottt — Spotify ASCII Art Companion.

Displays the album cover (or artist image) of your currently playing
Spotify track as real-time ASCII art in the terminal.

Usage:
    python run.py [--client-id ID] [--style STYLE] [--cols N]

Environment:
    SPOTIFY_CLIENT_ID   Your Spotify app's Client ID (required).
                        Create one at https://developer.spotify.com/dashboard
                        Set redirect URI to http://127.0.0.1:8888/callback

Controls:
    s / S       Next / previous ASCII art style
    c           Cycle color mode
    + / -       Increase / decrease art size
    0           Reset to auto size
    q           Quit
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Spottt — Spotify ASCII Art Companion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Styles: braille, block, classic, edge, particles, retro-art, terminal\n"
            "\nExample:\n"
            '  export SPOTIFY_CLIENT_ID="abc123..."\n'
            "  python run.py --style braille --cols 60\n"
        ),
    )
    parser.add_argument(
        "--client-id",
        help="Spotify Client ID (or set SPOTIFY_CLIENT_ID env var)",
    )
    parser.add_argument(
        "--style",
        choices=[
            "braille",
            "block",
            "classic",
            "edge",
            "particles",
            "retro-art",
            "terminal",
        ],
        default=None,
        help="Initial ASCII art style (default: braille)",
    )
    parser.add_argument(
        "--cols",
        type=int,
        default=0,
        help="ASCII art width in characters (0 = auto, default: auto)",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear stored Spotify tokens and exit",
    )

    args = parser.parse_args()

    if args.logout:
        from spottt.auth import SpotifyAuth

        SpotifyAuth.clear_tokens()
        print("Logged out. Tokens cleared.")
        return

    from spottt.app import SpotttApp

    app = SpotttApp(
        client_id=args.client_id,
        cols=args.cols,
        style=args.style,
    )
    app.run()


if __name__ == "__main__":
    main()
