#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Activity Logger - Logs all user activities and browser events
"""

import json
import time
import os
from urllib.parse import urlparse, parse_qs
from .config import CONFIG


class ActivityLogger:
    """Logs all user activities and browser events"""
    
    def __init__(self):
        self.log_file = CONFIG['log_file']
        self.log_format = CONFIG['log_format']
        self.enabled = CONFIG['log_enabled']
        
        # Create logs directory if needed
        if self.enabled:
            log_dir = os.path.dirname(self.log_file) if os.path.dirname(self.log_file) else 'logs'
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
    
    def _get_timestamp(self):
        """Get current timestamp"""
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    
    def _write_log(self, data):
        """Write log entry to file"""
        if not self.enabled:
            return
        
        try:
            # Ensure log directory exists
            log_dir = os.path.dirname(self.log_file) if os.path.dirname(self.log_file) else 'logs'
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            with open(self.log_file, 'a', encoding='utf-8') as f:
                if self.log_format == 'json':
                    log_entry = json.dumps(data, ensure_ascii=False)
                    f.write(log_entry + '\n')
                    f.flush()  # Force write to disk immediately
                else:
                    # Text format
                    timestamp = data.get('timestamp', self._get_timestamp())
                    event_type = data.get('type', 'unknown')
                    f.write(f"[{timestamp}] {event_type.upper()}: {json.dumps(data, ensure_ascii=False, indent=2)}\n")
                    f.flush()  # Force write to disk immediately
            
        except Exception as e:
            print(f"⚠️ [ERROR] Error writing log to {self.log_file}: {e}")
            import traceback
            traceback.print_exc()
    
    async def log_request(self, request):
        """Log HTTP request"""
        if not CONFIG['log_requests']:
            return
        
        # Extract query parameters from URL
        query_params = {}
        try:
            parsed_url = urlparse(request.url)
            if parsed_url.query:
                query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_url.query).items()}
        except:
            pass
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'request',
            'url': request.url,
            'method': request.method,
            'headers': dict(request.headers),
        }
        
        # Add query parameters if any
        if query_params:
            data['query_params'] = query_params
        
        # Try to get request body
        body_data = None
        body_type = None
        
        # First try post_data (synchronous, may be None)
        if request.post_data:
            post_data = request.post_data
            # Check if post_data is bytes or str
            if isinstance(post_data, bytes):
                try:
                    body_str = post_data.decode('utf-8', errors='ignore')
                    body_data = body_str
                    body_type = 'text'
                except:
                    # Binary data - encode as base64 for display
                    import base64
                    body_data = base64.b64encode(post_data).decode('ascii')
                    body_type = 'binary'
            elif isinstance(post_data, str):
                # Already a string
                body_data = post_data
                body_type = 'text'
            else:
                body_data = str(post_data)
                body_type = 'text'
        
        # If post_data is None, try post_data_buffer (property, not method)
        if body_data is None and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            try:
                if hasattr(request, 'post_data_buffer') and request.post_data_buffer:
                    post_buffer = request.post_data_buffer
                    if isinstance(post_buffer, bytes):
                        try:
                            body_str = post_buffer.decode('utf-8', errors='ignore')
                            body_data = body_str
                            body_type = 'text'
                        except:
                            # Binary data - encode as base64 for display
                            import base64
                            body_data = base64.b64encode(post_buffer).decode('ascii')
                            body_type = 'binary'
                    else:
                        body_data = str(post_buffer) if post_buffer else ''
                        body_type = 'text' if post_buffer else 'empty'
            except Exception as e:
                # If we can't get body, mark as unavailable
                if CONFIG['log_request_body']:
                    body_data = ''
                    body_type = 'empty'
        
        # Parse JSON if possible
        if body_data and body_type == 'text' and body_data != '<binary>':
            try:
                body_json = json.loads(body_data)
                data['body'] = body_json
                data['body_type'] = 'json'
            except:
                # Not JSON, keep as text
                data['body'] = body_data[:5000]  # Limit size
                data['body_type'] = 'text'
        elif body_data:
            data['body'] = body_data
            data['body_type'] = body_type
        elif request.method in ['POST', 'PUT', 'PATCH', 'DELETE'] and CONFIG['log_request_body']:
            # Log empty body for POST/PUT/PATCH/DELETE if explicitly enabled
            data['body'] = ''
            data['body_type'] = 'empty'
        
        self._write_log(data)
    
    async def log_response(self, response):
        """Log HTTP response"""
        if not CONFIG['log_requests']:
            return
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'response',
            'url': response.url,
            'status': response.status,
            'status_text': response.status_text,
            'headers': dict(response.headers),
        }
        
        if CONFIG['log_response_body']:
            try:
                body = await response.body()
                if isinstance(body, bytes):
                    body = body.decode('utf-8', errors='ignore')[:5000]  # Limit size
                data['body'] = body
            except:
                pass
        
        self._write_log(data)
    
    def log_cookies(self, cookies, action='read'):
        """Log cookies"""
        if not CONFIG['log_cookies']:
            return
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'cookies',
            'action': action,  # read, set, delete
            'cookies': cookies,
        }
        self._write_log(data)
    
    def log_storage(self, storage_type, key, value, action='get'):
        """Log localStorage or sessionStorage"""
        if storage_type == 'localStorage' and not CONFIG['log_localstorage']:
            return
        if storage_type == 'sessionStorage' and not CONFIG['log_sessionstorage']:
            return
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'storage',
            'storage_type': storage_type,
            'action': action,  # get, set, remove, clear
            'key': key,
            'value': value,
        }
        self._write_log(data)
    
    def log_console(self, message, level='log'):
        """Log console message"""
        if not CONFIG['log_console']:
            return
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'console',
            'level': level,  # log, error, warn, info, debug
            'message': str(message),
        }
        self._write_log(data)
    
    def log_interaction(self, interaction_type, details):
        """Log user interaction"""
        if not CONFIG['log_interactions']:
            return
        
        data = {
            'timestamp': self._get_timestamp(),
            'type': 'interaction',
            'interaction_type': interaction_type,  # click, type, scroll, navigate
            'details': details,
        }
        self._write_log(data)

