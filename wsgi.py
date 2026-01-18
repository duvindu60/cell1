#!/usr/bin/env python3
"""
WSGI entry point for production deployment
"""

from app import create_app
import os

# Set production environment
os.environ.setdefault('FLASK_ENV', 'production')

# Create application instance
application = create_app('production')

if __name__ == "__main__":
    application.run()
