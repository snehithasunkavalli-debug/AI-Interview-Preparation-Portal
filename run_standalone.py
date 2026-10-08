import os
import sys
import webbrowser
import threading
import time

def open_browser(url):
    time.sleep(1.5)
    print(f"[*] Opening browser at {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    url = f"http://localhost:{port}"
    
    # Launch browser thread
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    print("======================================================")
    print(f"[*] AI Interview Preparation Portal (Standalone)")
    print(f"[*] Access the app at: {url}")
    print(f"[*] Running directly on your computer (No IDE needed)")
    print("======================================================")

    # Use production WSGI server 'waitress' if installed, otherwise Flask dev server
    try:
        from waitress import serve
        from app import app
        print(f"[*] Running with Waitress Production Server on port {port}...")
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        from app import app
        print(f"[*] Running with Flask Server on port {port}...")
        app.run(host="0.0.0.0", port=port, debug=False)

