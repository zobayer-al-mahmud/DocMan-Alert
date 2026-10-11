"""
Production entry point for DocMan Alert.

Use this instead of run.py for production deployment — it serves the app
through Waitress (a production-grade WSGI server) instead of Flask's
built-in development server.

Run manually:
    venv\\Scripts\\python.exe serve.py

Or set this as the target of a Windows Task Scheduler task that runs
"At system startup" so the app comes back up automatically after a reboot.
"""
from waitress import serve
from app import create_app

app = create_app()

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=5001, threads=12)
