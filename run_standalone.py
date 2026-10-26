import os
import sys

# Ensure Windows stdout does not crash on unicode / emoji characters
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import webbrowser
import threading
import time
import socket

# Ensure working directory is script directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def find_available_port(start_port=8080):
    port = start_port
    while port < start_port + 20:
        if not is_port_in_use(port):
            return port
        port += 1
    return start_port

def open_browser(url):
    time.sleep(1.2)
    print(f"[*] Opening browser: {url}")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"[!] Could not open browser automatically: {e}")

if __name__ == "__main__":
    requested_port = int(os.environ.get("PORT", 8080))
    port = find_available_port(requested_port)
    
    url = f"http://127.0.0.1:{port}"
    
    # Launch browser thread
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    print("======================================================")
    print("  [*] AI Interview Preparation Portal (Standalone)     ")
    print(f"  Access URL : {url}")
    print(f"  Alternate  : http://localhost:{port}")
    print("======================================================")
    print("[*] Server is running. Press CTRL+C to stop.")

    # Import app
    from app import app

    try:
        from waitress import serve
        print(f"[*] Starting high-performance Waitress server on 127.0.0.1:{port}...")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except Exception as e:
        print(f"[*] Starting Flask built-in server on port {port} (Reason: {e})...")
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
