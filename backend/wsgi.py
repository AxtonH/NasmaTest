import sys
import logging

# Configure logging before importing app (for Railway)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)

try:
    # Production (project root): backend is a package
    from backend.app import create_app
except ModuleNotFoundError:
    # Local (cwd = backend/): import from sibling module
    from app import create_app

# WSGI entrypoint for production servers like Gunicorn
app = create_app()

# Log that app is ready
app.logger.info("WSGI application loaded successfully")
