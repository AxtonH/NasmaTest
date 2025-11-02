# Railway Logging Configuration Guide

## Environment Variables

To ensure logs appear on Railway, set these environment variables in your Railway project settings:

### Required for Logging
- `PYTHONUNBUFFERED=1` - Forces Python to output immediately (no buffering)
- `VERBOSE_LOGS=true` - Enable verbose debug logs (optional, for debugging)
- `DEBUG_BOT_LOGIC=true` - Enable bot logic logs (optional, for debugging)
- `DEBUG_ODOO_DATA=true` - Enable Odoo data logs (optional, for debugging)

### How to Set in Railway:
1. Go to your Railway project dashboard
2. Click on your service
3. Go to "Variables" tab
4. Add the variables above

## What Was Changed:

1. **app.py**: 
   - Added proper Python logging configuration
   - Configured Flask logger to stdout
   - Added `flush=True` to all print statements
   - Always log errors/warnings regardless of debug flags

2. **wsgi.py**:
   - Added logging configuration before app import
   - Ensures logs appear when Gunicorn starts

3. **Procfile**:
   - Added Gunicorn logging flags:
     - `--log-level info` - Set log level
     - `--access-logfile -` - Log access to stdout
     - `--error-logfile -` - Log errors to stdout
     - `--capture-output` - Capture stdout/stderr
     - `--enable-stdio-inheritance` - Inherit stdio from parent

## Testing:

After deploying, check Railway logs:
1. Go to Railway dashboard
2. Click on your service
3. Click "Deployments" tab
4. Click on latest deployment
5. View "Logs" tab

You should now see:
- Startup messages
- Request logs
- Error messages
- Debug messages (if flags enabled)

