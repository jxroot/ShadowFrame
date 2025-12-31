#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Main entry point for MITM Browser
Run with: python run.py
"""

import argparse
import uvicorn
from backend.app.config import CONFIG

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='MITM Browser - Real-time streaming browser',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start with default settings
  python run.py

  # Custom port
  python run.py --port 8080

  # Custom host and port
  python run.py --host 0.0.0.0 --port 8080
        """
    )
    
    parser.add_argument('--port', '-p', type=int, default=CONFIG['http_port'],
                       help=f'Web server port (default: {CONFIG["http_port"]})')
    parser.add_argument('--host', default=CONFIG['http_host'],
                       help=f'Web server host (default: {CONFIG["http_host"]})')
    
    args = parser.parse_args()
    
    # Override config with command line arguments if provided
    if args.port != CONFIG['http_port']:
        CONFIG['http_port'] = args.port
    if args.host != CONFIG['http_host']:
        CONFIG['http_host'] = args.host
    
    print(f"🚀 Starting MITM Browser on {args.host}:{args.port}")
    
    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
        timeout_keep_alive=5,  # Close idle connections after 5 seconds
        timeout_graceful_shutdown=2.0  # Force shutdown after 2 seconds
    )

