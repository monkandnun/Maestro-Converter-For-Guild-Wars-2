import socket, threading, time
import uvicorn, webview
from app import app

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

port = free_port()
server = uvicorn.Server(uvicorn.Config(
    app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

webview.settings["ALLOW_DOWNLOADS"] = True
webview.create_window("Maestro Converter", f"http://127.0.0.1:{port}",
                      width=1100, height=850)
webview.start()
server.should_exit = True