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
        """Open browser after a short delay to ensure server is ready"""
        time.sleep(2)  # Wait for server to start
        try:
            webbrowser.open('http://localhost:5000')
            print("🌐 Opening browser automatically...")
        except Exception as e:
            print(f"⚠️  Could not open browser automatically: {e}")
            print("   Please manually open: http://localhost:5000")
    
    # Start browser in a separate thread
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    # Start the Flask application with proper working directory
    try:
        subprocess.run([sys.executable, app_path], cwd=backend_dir, check=True)
    except KeyboardInterrupt:
        print("\n👋 Shutting down server...")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"❌ Error starting server: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())
