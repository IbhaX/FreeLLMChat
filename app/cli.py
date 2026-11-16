"""`chat` command / `python -m chat`: start the gateway and open the chat UI."""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

PORT = int(os.environ.get("PORT", "5000"))
HOST = "127.0.0.1"


def _open_browser(url: str) -> None:
    # Termux / Android: no webbrowser support — use the Termux opener or am.
    if "com.termux" in sys.prefix or shutil.which("termux-open-url"):
        if shutil.which("termux-open-url"):
            subprocess.run(["termux-open-url", url], check=False)
            return
        subprocess.run(
            ["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
            check=False,
        )
        return
    try:
        # new=0: reuse the existing browser window where the OS supports it
        webbrowser.get("google-chrome").open(url, new=0)
    except webbrowser.Error:
        webbrowser.open(url, new=0)


def _server_running() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            pass
    except OSError:
        return False
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{PORT}/healthz", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        # Something else holds the port, or health check missing — treat as
        # "not ours" so we don't silently claim it's up.
        return False


def _open_when_up(url: str) -> None:
    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    _open_browser(url)


def main() -> None:
    url = f"http://localhost:{PORT}/chat"
    if _server_running():
        # Already running — reopening the URL would spawn a duplicate tab
        # (browsers can't navigate an arbitrary existing tab), so just point
        # the user at it instead of stacking tabs.
        print(f"Nova is already running — open {url} in your existing tab.")
        return
    threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
