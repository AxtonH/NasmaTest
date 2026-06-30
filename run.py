#!/usr/bin/env python3
"""
Launcher script for the Odoo Chatbot Assistant
"""

import os
import sys
import subprocess
import webbrowser
import time
import threading

def main():
    """Main launcher function"""
    print("🤖 Starting Nasma AI Assistant...")
    print("=" * 50)
    
    # Check if virtual environment is activated
    if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("⚠️  Virtual environment not detected. Consider using a virtual environment.")
        print("   Create one with: python -m venv venv")
        print("   Activate with: venv\\Scripts\\activate (Windows) or source venv/bin/activate (Unix)")
        print()
    
    # Check if dependencies are installed
    try:
        import flask
        import openai
        print("✅ Dependencies found")
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("   Install with: pip install -r requirements.txt")
        return 1
    
    # Get absolute paths
    project_root = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(project_root, 'backend')
    app_path = os.path.join(backend_dir, 'app.py')
    
    if not os.path.exists(backend_dir):
        print("❌ Backend directory not found")
        return 1
    
    if not os.path.exists(app_path):
        print("❌ app.py not found in backend directory")
        return 1
    
    print("🚀 Starting Flask server...")
    print("   URL: http://localhost:5000")
    print("   Press Ctrl+C to stop the server")
    print("=" * 50)
    
    def open_browser():
        """Open the browser only once the server actually responds.

        Polls the port instead of using a fixed sleep so a slow cold import
        (e.g. first-run compilation of the openai package) can't race the
        browser launch. Falls back to opening anyway after the timeout.
        """
        import socket
        deadline = time.time() + 30  # generous: cold first-run imports are slow
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', 5000), timeout=1):
                    break  # server is accepting connections
            except OSError:
                time.sleep(0.5)
        try:
            webbrowser.open('http://localhost:5000')
            print("🌐 Opening browser automatically...")
        except Exception as e:
            print(f"⚠️  Could not open browser automatically: {e}")
            print("   Please manually open: http://localhost:5000")

    # Start browser in a separate thread
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    # Give the Flask child its own process group so console signals
    # (e.g. the CTRL_C_EVENT a browser launch can emit on the shared
    # Windows console) cannot interrupt it mid-import. The parent still
    # handles Ctrl+C and shuts the child down cleanly.
    popen_kwargs = {'cwd': backend_dir}
    if os.name == 'nt':
        popen_kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs['start_new_session'] = True

    proc = None
    try:
        proc = subprocess.Popen([sys.executable, app_path], **popen_kwargs)
        returncode = proc.wait()
        if returncode not in (0, None):
            print(f"❌ Server exited with code {returncode}")
            return 1
    except KeyboardInterrupt:
        print("\n👋 Shutting down server...")
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 0

if __name__ == '__main__':
    sys.exit(main())
