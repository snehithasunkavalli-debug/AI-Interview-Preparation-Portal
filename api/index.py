import os
import sys

# Ensure the project root directory is on the Python module search path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Expose the Flask WSGI application callable for Vercel Serverless runtime
from app import app
