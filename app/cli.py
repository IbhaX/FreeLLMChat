"""`chat` command / `python -m chat`: start the gateway and open the chat UI."""

import os
import socket
import threading
import time
import webbrowser

import uvicorn

PORT = int(os.environ.get("PORT", "8000"))
HOST = "127.0.0.1"


def _open_when_up(url: str) -> None:
    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    try:
        webbrowser.get("google-chrome").open(url)
    except webbrowser.Error:
        webbrowser.open(url)


def main() -> None:
    url = f"http://localhost:{PORT}/chat"
    threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
