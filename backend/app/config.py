#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration management for MITM Browser
Default configuration values (no environment variables)
"""

# Default configuration values
CONFIG = {
    'http_port': 8080,
    'http_host': '127.0.0.1',
    'viewport_width': 1024,
    'viewport_height': 768,
    'headless': True,
    'streaming_fps': 15,
    'screenshot_format': 'jpeg',
    'jpeg_quality': 60,
    'page_info_interval': 10,
    'typing_delay': 10,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'browser_args': ['--no-sandbox', '--disable-setuid-sandbox'],
    'debug': False,
    # Logging configuration
    'log_enabled': True,
    'log_file': 'logs/activity.log',
    'log_requests': True,
    'log_cookies': True,
    'log_localstorage': True,
    'log_sessionstorage': True,
    'log_console': True,
    'log_interactions': True,
    'log_response_body': False,
    'log_request_body': False,
    'log_format': 'json',
    'default_url': '',
}
