try:
    # Production (project root): backend is a package
    from backend.app import create_app
except ModuleNotFoundError:
    # Local (cwd = backend/): import from sibling module
    from app import create_app

# WSGI entrypoint for production servers like Gunicorn
app = create_app()


