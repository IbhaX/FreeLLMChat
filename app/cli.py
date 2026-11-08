"""`chat` command / `python -m chat`: start the gateway and open the chat UI."""

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

PORT = int(os.environ.get("PORT", "8000"))
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
        webbrowser.get("google-chrome").open(url)
    except webbrowser.Error:
        webbrowser.open(url)


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
    threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
