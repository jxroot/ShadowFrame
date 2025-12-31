#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI application for MITM Browser
"""

import asyncio
import json
import time
import os
import urllib.parse
from contextlib import asynccontextmanager
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import aiofiles
from pydantic import TypeAdapter, ValidationError

from .config import CONFIG
from .activity_logger import ActivityLogger
from .browser_manager import BrowserManager, set_activity_logger
from .schemas import LogsResponse, WSCommand, WSErrorMessage, WSInitMessage, WSNavigateResult, Viewport

# Global state
browser_manager: BrowserManager = None
activity_logger: ActivityLogger = None
ws_clients: Set[WebSocket] = set()
streaming_active = False
stream_task = None
monitor_tasks: list = []  # Track monitoring tasks for proper shutdown


def init_activity_logger():
    """Initialize activity logger"""
    global activity_logger
    activity_logger = ActivityLogger()
    set_activity_logger(activity_logger)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    global browser_manager, streaming_active, stream_task, monitor_tasks, ws_clients
    
    # Startup
    print("🚀 Starting MITM Browser...")
    
    # Initialize activity logger
    init_activity_logger()
    
    # Initialize browser
    browser_manager = BrowserManager()
    success = await browser_manager.initialize()
    
    if not success:
        print("❌ Failed to initialize browser")
        yield
        return
    
    # Start streaming task
    streaming_active = True
    stream_task = asyncio.create_task(stream_frames())
    monitor_tasks = []
    
    # Start monitoring tasks
    if CONFIG['log_cookies']:
        monitor_tasks.append(asyncio.create_task(monitor_cookies()))
    
    if CONFIG['log_localstorage'] or CONFIG['log_sessionstorage']:
        monitor_tasks.append(asyncio.create_task(monitor_storage()))
    
    print(f"✅ Browser initialized and streaming started")
    print(f"🌐 Web interface: http://{CONFIG['http_host']}:{CONFIG['http_port']}")
    
    yield
    
    # Shutdown
    print("🛑 Shutting down...")
    streaming_active = False
    
    # Close all WebSocket connections first (with timeout)
    if ws_clients:
        print(f"Closing {len(ws_clients)} WebSocket connections...")
        clients_to_close = list(ws_clients)
        ws_clients.clear()  # Clear immediately to prevent new operations
        close_tasks = []
        for client in clients_to_close:
            async def close_client(c):
                try:
                    await asyncio.wait_for(c.close(), timeout=0.5)
                except (asyncio.TimeoutError, Exception):
                    pass
            close_tasks.append(close_client(client))
        
        # Wait for all closes with timeout
        try:
            await asyncio.wait_for(asyncio.gather(*close_tasks, return_exceptions=True), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    
    # Cancel all monitoring tasks (with shorter timeout)
    if monitor_tasks:
        print(f"Cancelling {len(monitor_tasks)} monitoring tasks...")
        for task in monitor_tasks:
            if not task.done():
                task.cancel()
        # Wait for tasks to cancel (with shorter timeout)
        try:
            await asyncio.wait_for(
                asyncio.gather(*monitor_tasks, return_exceptions=True),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass
    
    # Cancel streaming task (with shorter timeout)
    if stream_task and not stream_task.done():
        print("Cancelling streaming task...")
        stream_task.cancel()
        try:
            await asyncio.wait_for(stream_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            pass
    
    # Close browser after all tasks stop (with timeout)
    if browser_manager:
        try:
            print("Closing browser...")
            await asyncio.wait_for(browser_manager.close(), timeout=2.0)
        except asyncio.TimeoutError:
            print("⚠️  Browser close timeout, forcing shutdown...")
        except Exception as e:
            print(f"⚠️  Error closing browser: {e}")
    
    print("✅ Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="MITM Browser",
    description="Real-time browser streaming with logging",
    lifespan=lifespan
)

# Add CORS middleware
allowed_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Calculate frontend dist path
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'frontend', 'dist')


async def stream_frames():
    """Stream browser frames to all connected clients"""
    global browser_manager, ws_clients, streaming_active
    
    frame_count = 0
    
    while streaming_active and browser_manager and browser_manager.initialized:
        try:
            # Check if browser/page is still available
            if not browser_manager.page:
                await asyncio.sleep(0.1)
                continue
            
            # Check if page is closed
            try:
                if browser_manager.page.is_closed():
                    await asyncio.sleep(0.1)
                    continue
            except:
                # Page might be closed, skip this iteration
                await asyncio.sleep(0.1)
                continue
            
            # Get screenshot (only if clients connected)
            if not ws_clients:
                await asyncio.sleep(0.1)
                continue
            
            screenshot_b64 = await browser_manager.get_screenshot()
            if not screenshot_b64:
                # Sleep a bit if screenshot failed (might be closed)
                await asyncio.sleep(0.1)
                continue
            
            # Get page info (less frequently to save CPU)
            page_info = None
            if frame_count % CONFIG['page_info_interval'] == 0:
                page_info = await browser_manager.get_page_info()
            
            # Create frame data (minimal JSON)
            frame_data = {
                'f': screenshot_b64,  # Short key names
                'fmt': browser_manager.screenshot_format,  # Format for client
                'i': page_info if page_info else None,
                't': time.time(),
                'id': frame_count
            }
            
            # Send to all connected clients concurrently (avoid one slow client blocking others)
            message = json.dumps(frame_data)

            async def _safe_send(client: WebSocket, payload: str):
                try:
                    # Check if client is still connected
                    if client.client_state.name != "CONNECTED":
                        return Exception("Client not connected")
                    
                    # Send with timeout to prevent hanging
                    await asyncio.wait_for(client.send_text(payload), timeout=0.5)
                    return None  # Success
                except asyncio.TimeoutError:
                    return Exception("Send timeout")
                except (RuntimeError, ConnectionError, OSError) as e:
                    # Connection errors - client disconnected
                    return e
                except Exception as e:
                    error_str = str(e).lower()
                    # Check for connection-related errors
                    if any(keyword in error_str for keyword in ["closed", "disconnect", "connection", "socket"]):
                        return e
                    # Other errors - don't disconnect, just return None
                    return None

            clients = list(ws_clients)
            if clients:
                results = await asyncio.gather(
                    *[_safe_send(client, message) for client in clients],
                    return_exceptions=True,
                )

                disconnected = {client for client, err in zip(clients, results) if err is not None}
                
                # Remove disconnected clients
                if disconnected:
                    ws_clients -= disconnected
            
            frame_count += 1
            # Stream at configured FPS (optimized sleep)
            sleep_time = 1.0 / CONFIG['streaming_fps']
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            
        except Exception as e:
            print(f"⚠️  Streaming error: {e}")
            await asyncio.sleep(0.1)


async def monitor_cookies():
    """Periodically monitor cookies for changes"""
    global browser_manager, streaming_active
    
    last_cookies = []
    
    while streaming_active and browser_manager and browser_manager.initialized:
        try:
            # Check if browser is still connected
            if browser_manager.context:
                try:
                    if browser_manager.context.browser and not browser_manager.context.browser.is_connected():
                        break  # Browser disconnected, exit loop
                except:
                    break  # Can't check connection, exit loop
            
            if browser_manager.context and CONFIG['log_cookies']:
                current_cookies = await browser_manager.get_cookies()
                
                # Check if cookies changed
                if current_cookies != last_cookies:
                    if last_cookies and activity_logger:  # Only log if we had previous cookies
                        activity_logger.log_cookies(current_cookies, action='read')
                    last_cookies = current_cookies.copy() if current_cookies else []
            
            await asyncio.sleep(2)  # Check every 2 seconds
        except asyncio.CancelledError:
            # Task was cancelled, exit gracefully
            break
        except Exception as e:
            error_str = str(e).lower()
            # If browser is closed or connection error, exit loop
            if "closed" in error_str or "connection" in error_str or "disconnect" in error_str:
                break
            # Only log other errors if debug is enabled
            if CONFIG['debug']:
                print(f"⚠️  Cookie monitoring error: {e}")
            await asyncio.sleep(2)


async def monitor_storage():
    """Periodically monitor localStorage and sessionStorage for changes"""
    global browser_manager, streaming_active
    
    last_local_storage = {}
    last_session_storage = {}
    
    while streaming_active and browser_manager and browser_manager.initialized:
        try:
            # Check if browser is still connected
            if browser_manager.context:
                try:
                    if browser_manager.context.browser and not browser_manager.context.browser.is_connected():
                        break  # Browser disconnected, exit loop
                except:
                    break  # Can't check connection, exit loop
            
            if browser_manager.page:
                # Check if page is closed
                try:
                    if browser_manager.page.is_closed():
                        break  # Page closed, exit loop
                except:
                    break  # Can't check page, exit loop
                
                # Check localStorage
                if CONFIG['log_localstorage']:
                    try:
                        current_local_storage = await browser_manager.get_local_storage()
                        if current_local_storage != last_local_storage:
                            # Log new or changed items
                            if activity_logger:
                                for key, value in current_local_storage.items():
                                    if key not in last_local_storage or last_local_storage[key] != value:
                                        activity_logger.log_storage('localStorage', key, value, 'get')
                                # Log removed items
                                for key in last_local_storage:
                                    if key not in current_local_storage:
                                        activity_logger.log_storage('localStorage', key, None, 'remove')
                            last_local_storage = current_local_storage.copy()
                    except Exception as e:
                        error_str = str(e).lower()
                        if "closed" in error_str or "connection" in error_str:
                            break  # Browser closed, exit loop
                        if CONFIG['debug']:
                            print(f"⚠️  Error monitoring localStorage: {e}")
                
                # Check sessionStorage
                if CONFIG['log_sessionstorage']:
                    try:
                        current_session_storage = await browser_manager.get_session_storage()
                        if current_session_storage != last_session_storage:
                            # Log new or changed items
                            if activity_logger:
                                for key, value in current_session_storage.items():
                                    if key not in last_session_storage or last_session_storage[key] != value:
                                        activity_logger.log_storage('sessionStorage', key, value, 'get')
                                # Log removed items
                                for key in last_session_storage:
                                    if key not in current_session_storage:
                                        activity_logger.log_storage('sessionStorage', key, None, 'remove')
                            last_session_storage = current_session_storage.copy()
                    except Exception as e:
                        error_str = str(e).lower()
                        if "closed" in error_str or "connection" in error_str:
                            break  # Browser closed, exit loop
                        if CONFIG['debug']:
                            print(f"⚠️  Error monitoring sessionStorage: {e}")
            
            await asyncio.sleep(2)  # Check every 2 seconds
        except asyncio.CancelledError:
            # Task was cancelled, exit gracefully
            break
        except Exception as e:
            error_str = str(e).lower()
            # If browser is closed or connection error, exit loop
            if "closed" in error_str or "connection" in error_str or "disconnect" in error_str:
                break
            # Only log other errors if debug is enabled
            if CONFIG['debug']:
                print(f"⚠️  Storage monitoring error: {e}")
            await asyncio.sleep(2)


# API routes - must be defined before catch-all routes
@app.get("/api/logs")
async def get_logs(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    type: str | None = None,
    method: str | None = None,
    search: str | None = None,
):
    """Get logs API endpoint
    
    Query params:
    - limit: Maximum number of logs to return (1-5000, default: 1000)
    - offset: Number of logs to skip (default: 0)
    - type: Filter by log type (request, response, interaction, etc.)
    - method: Filter by HTTP method (GET, POST, PUT, PATCH, DELETE, etc.)
    - search: Search in log content (case-insensitive)
    """
    try:
        log_file = CONFIG['log_file']
        
        logs = []
        filtered_logs = []
        if os.path.exists(log_file):
            # First, read all logs and apply filters
            async with aiofiles.open(log_file, 'r', encoding='utf-8') as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        log_entry = json.loads(line)
                        # Filter by type
                        if type and log_entry.get('type') != type:
                            continue
                        # Filter by method (for request/response logs)
                        if method:
                            method_upper = method.upper()
                            # Check method in root level (for request_response type) or in request object
                            log_method = log_entry.get('method')
                            if not log_method:
                                # Try to get from request object
                                request_data = log_entry.get('request')
                                if request_data:
                                    log_method = request_data.get('method')
                            if not log_method or str(log_method).upper() != method_upper:
                                continue
                        # Filter by search
                        if search:
                            search_lower = search.lower()
                            log_str = json.dumps(log_entry, ensure_ascii=False).lower()
                            if search_lower not in log_str:
                                continue
                        filtered_logs.append(log_entry)
                    except json.JSONDecodeError:
                        continue
            
            # Apply offset and limit after filtering
            total_filtered = len(filtered_logs)
            logs = filtered_logs[offset:offset + limit]
        
        total_count = len(filtered_logs) if 'filtered_logs' in locals() else len(logs)
        return LogsResponse(
            success=True,
            logs=logs,
            total=total_count,
            offset=offset,
            limit=limit,
        ).model_dump()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=LogsResponse(
                success=False,
                logs=[],
                total=0,
                offset=offset,
                limit=limit,
                error=str(e),
            ).model_dump(),
        )


@app.get("/frame.jpg")
async def get_frame_jpg():
    """
    Single-frame endpoint (pure image, no HTML).
    Useful for quick checks / embedding.
    """
    if not browser_manager:
        return JSONResponse(status_code=503, content={"error": "Browser not initialized"})
    frame = await browser_manager.get_screenshot_bytes(fmt="jpeg")
    if not frame:
        return JSONResponse(status_code=503, content={"error": "Frame not available"})
    return Response(content=frame, media_type="image/jpeg")


@app.get("/stream")
async def stream_mjpeg(
    request: Request,
    fps: int = Query(None, ge=1, le=120),
    width: int = Query(None, ge=100, le=3840),
    height: int = Query(None, ge=100, le=2160),
    quality: int = Query(None, ge=1, le=100),
    format: str = Query(None, regex="^(jpeg|png)$")
):
    """
    Pure MJPEG browser stream endpoint (view-only, no interactions, no URL changes).
    Shows current page content only - does not accept URL parameter.
    
    Query params:
    - fps: Frames per second (1-120, default: 15)
    - width: Viewport width for display (100-3840, default: 1024)
    - height: Viewport height for display (100-2160, default: 768)
    - quality: JPEG quality (1-100, default: 60)
    - format: Screenshot format 'jpeg' or 'png' (default: 'jpeg')
    
    Examples:
    - /stream?fps=20
    - /stream?fps=60&width=1920&height=1080&quality=80
    - /stream?fps=30&format=png&quality=90
    """
    if not browser_manager:
        return JSONResponse(status_code=503, content={"error": "Browser not initialized"})

    # Use query params or defaults
    stream_fps = fps if fps is not None else CONFIG['streaming_fps']
    stream_quality = quality if quality is not None else CONFIG['jpeg_quality']
    stream_format = (format or CONFIG['screenshot_format']).lower()

    # Always return pure MJPEG stream - no navigation, no HTML wrapper
    # Just stream whatever is currently loaded in the browser
    boundary = b"frame"
    frame_interval = 1.0 / float(stream_fps)
    media_type = f"multipart/x-mixed-replace; boundary=frame"
    content_type = f"image/{stream_format}"

    async def gen():
        try:
            while streaming_active:  # Check global streaming_active flag
                if await request.is_disconnected():
                    break

                img = await browser_manager.get_screenshot_bytes(fmt=stream_format, quality=stream_quality)
                if img:
                    headers = (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: " + content_type.encode("ascii") + b"\r\n"
                        b"Content-Length: " + str(len(img)).encode("ascii") + b"\r\n\r\n"
                    )
                    yield headers
                    yield img
                    yield b"\r\n"

                await asyncio.sleep(frame_interval)
        except asyncio.CancelledError:
            # Task was cancelled during shutdown
            pass

        yield b"--" + boundary + b"--\r\n"

    return StreamingResponse(
        gen(),
        media_type=media_type,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connection"""
    global ws_clients, browser_manager
    
    await websocket.accept()
    ws_clients.add(websocket)
    
    try:
        # Send initial connection message
        try:
            page_info = await browser_manager.get_page_info() if browser_manager else None
            init_payload = WSInitMessage(
                info=page_info,
                default_url=None,  # No default URL - must be provided via query params
                viewport=Viewport(
                    width=CONFIG["viewport_width"],
                    height=CONFIG["viewport_height"],
                ),
            ).model_dump()
            await websocket.send_json(init_payload)
        except Exception as e:
            print(f"⚠️  Error sending init message: {e}")
            return
        
        # Handle messages from client
        while True:
            try:
                data = await websocket.receive_json()

                # Validate WS command payload
                try:
                    cmd = TypeAdapter(WSCommand).validate_python(data)
                except ValidationError as ve:
                    try:
                        await websocket.send_json(
                            WSErrorMessage(
                                error="Invalid command payload",
                                detail=ve.errors(),
                            ).model_dump()
                        )
                    except Exception:
                        break
                    continue

                if cmd.action == 'navigate':
                    if browser_manager:
                        success = await browser_manager.navigate(cmd.url)
                        try:
                            await websocket.send_json(WSNavigateResult(success=success).model_dump())
                        except Exception:
                            break  # Connection closed

                elif cmd.action == 'click':
                    if browser_manager:
                        await browser_manager.click(cmd.x, cmd.y)

                elif cmd.action == 'type':
                    if browser_manager:
                        await browser_manager.type_text(cmd.text)

                elif cmd.action == 'key':
                    if browser_manager:
                        await browser_manager.press_key(cmd.key)

                elif cmd.action == 'scroll':
                    if browser_manager:
                        await browser_manager.scroll(cmd.deltaX, cmd.deltaY)
                    
            except WebSocketDisconnect:
                # Normal disconnect, break out of loop
                break
            except RuntimeError as e:
                # Connection closed error
                if "disconnect" in str(e).lower() or "receive" in str(e).lower():
                    break
                print(f"⚠️  WebSocket runtime error: {e}")
                break
            except Exception as e:
                # Other errors - log but don't break unless it's a connection error
                error_str = str(e).lower()
                if "disconnect" in error_str or "receive" in error_str or "connection" in error_str:
                    break
                print(f"⚠️  WebSocket message error: {e}")
                await asyncio.sleep(0.1)
    
    except WebSocketDisconnect:
        # Normal disconnect
        pass
    except Exception as e:
        # Log unexpected errors
        error_str = str(e).lower()
        if "disconnect" not in error_str and "receive" not in error_str:
            print(f"⚠️  WebSocket error: {e}")
    finally:
        # Always remove from clients set
        ws_clients.discard(websocket)


# Mount static files and serve frontend if built
if os.path.exists(frontend_dist):
    # Mount static files directories if they exist (must be before catch-all routes)
    static_dir = os.path.join(frontend_dist, "static")
    assets_dir = os.path.join(frontend_dist, "assets")
    
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        print(f"✅ Mounted /static from {static_dir}")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        print(f"✅ Mounted /assets from {assets_dir}")
    else:
        print(f"⚠️  Assets directory not found: {assets_dir}")
    
    # Serve index.html for root - try React first, fallback to embedded HTML if JS files don't load
    @app.get("/", response_class=HTMLResponse)
    async def serve_index(
        url: str = Query(..., description="URL to navigate to (required)"),
        fps: int = Query(None, ge=1, le=120),
        width: int = Query(None, ge=100, le=3840),
        height: int = Query(None, ge=100, le=2160),
        quality: int = Query(None, ge=1, le=100),
        format: str = Query(None, regex="^(jpeg|png)$"),
        # Logging parameters
        log_enabled: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_requests: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_cookies: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_localstorage: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_sessionstorage: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_console: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_interactions: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_response_body: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
        log_request_body: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$")
    ):
        """
        Main page endpoint with query parameters.
        
        Query params:
        - url: URL to navigate to (REQUIRED)
        - fps: Frames per second (1-120, default: 15)
        - width: Viewport width (100-3840, default: 1024)
        - height: Viewport height (100-2160, default: 768)
        - quality: JPEG quality (1-100, default: 60)
        - format: Screenshot format 'jpeg' or 'png' (default: 'jpeg')
        - log_enabled: Enable/disable logging (true/false, default: true)
        - log_requests: Log HTTP requests/responses (true/false, default: true)
        - log_cookies: Log cookies (true/false, default: true)
        - log_localstorage: Log localStorage (true/false, default: true)
        - log_sessionstorage: Log sessionStorage (true/false, default: true)
        - log_console: Log console messages (true/false, default: true)
        - log_interactions: Log user interactions (true/false, default: true)
        - log_response_body: Log response bodies (true/false, default: false)
        - log_request_body: Log request bodies (true/false, default: false)
        
        Examples:
        - /?url=https://google.com
        - /?url=https://example.com&fps=30&width=1920&height=1080
        - /?url=https://example.com&log_requests=false&log_cookies=false
        """
        # Use query params or defaults
        stream_fps = fps if fps is not None else CONFIG['streaming_fps']
        stream_width = width if width is not None else CONFIG['viewport_width']
        stream_height = height if height is not None else CONFIG['viewport_height']
        stream_quality = quality if quality is not None else CONFIG['jpeg_quality']
        stream_format = (format or CONFIG['screenshot_format']).lower()
        
        # Update logging configuration if provided
        if browser_manager and browser_manager.initialized:
            log_params = {}
            if log_enabled is not None:
                log_params['log_enabled'] = log_enabled
            if log_requests is not None:
                log_params['log_requests'] = log_requests
            if log_cookies is not None:
                log_params['log_cookies'] = log_cookies
            if log_localstorage is not None:
                log_params['log_localstorage'] = log_localstorage
            if log_sessionstorage is not None:
                log_params['log_sessionstorage'] = log_sessionstorage
            if log_console is not None:
                log_params['log_console'] = log_console
            if log_interactions is not None:
                log_params['log_interactions'] = log_interactions
            if log_response_body is not None:
                log_params['log_response_body'] = log_response_body
            if log_request_body is not None:
                log_params['log_request_body'] = log_request_body
            
            if log_params:
                browser_manager.set_logging_config(**log_params)
                # Re-setup logging with new config
                browser_manager._setup_logging()
        
        # Navigate to URL if browser is ready
        if browser_manager and browser_manager.initialized:
            try:
                await browser_manager.navigate(url)
                await asyncio.sleep(0.5)  # Wait a bit for page to load
            except Exception as e:
                if CONFIG['debug']:
                    print(f"⚠️  Auto-navigation error: {e}")
        
        index_path = os.path.join(frontend_dist, "index.html")
        assets_path = os.path.join(frontend_dist, "assets")
        
        # Only serve React if both index.html AND assets directory exist
        if os.path.exists(index_path) and os.path.exists(assets_path):
            return FileResponse(index_path)
        else:
            # Fallback to embedded HTML if React files incomplete
            print("⚠️  Using fallback HTML (React files incomplete or not loading)")
            html_content = get_stream_html(
                fps=stream_fps,
                width=stream_width,
                height=stream_height,
                quality=stream_quality,
                format=stream_format,
                url=url
            )
            return HTMLResponse(content=html_content)
    
    # Catch-all route for SPA routing (must be last, after static mounts)
    # Note: /assets/ and /static/ paths are handled by mounts above, so they won't reach here
    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """Serve React frontend - handle SPA routing"""
        # Don't serve API routes or WebSocket (these are handled by specific routes above)
        if path.startswith("api/") or path.startswith("ws"):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        
        # Note: /assets/ and /static/ paths are handled by StaticFiles mounts above
        # FastAPI will match those mounts first, so they won't reach this catch-all route
        
        # Try to serve the actual file if it exists (for other static files like favicon, etc.)
        file_path = os.path.join(frontend_dist, path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        
        # For SPA routing, serve index.html for all other paths
        index_path = os.path.join(frontend_dist, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        else:
            # Fallback to embedded HTML if React files don't exist
            default_url = ''
            html = get_index_html()
            return HTMLResponse(content=html.replace('{{DEFAULT_URL}}', default_url))
else:
    # Fallback to embedded HTML if frontend not built
    print("⚠️  Frontend not built, using fallback HTML")
    
@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def serve_index_fallback(
    url: str = Query(..., description="URL to navigate to (required)"),
    fps: int = Query(None, ge=1, le=120),
    width: int = Query(None, ge=100, le=3840),
    height: int = Query(None, ge=100, le=2160),
    quality: int = Query(None, ge=1, le=100),
    format: str = Query(None, regex="^(jpeg|png)$"),
    # Logging parameters
    log_enabled: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_requests: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_cookies: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_localstorage: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_sessionstorage: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_console: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_interactions: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_response_body: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$"),
    log_request_body: str = Query(None, regex="^(true|false|1|0|yes|no|on|off)$")
):
    """
    Main page endpoint (fallback when frontend not built).
    
    Query params:
    - url: URL to navigate to (REQUIRED)
    - fps: Frames per second (1-120, default: 15)
    - width: Viewport width (100-3840, default: 1024)
    - height: Viewport height (100-2160, default: 768)
    - quality: JPEG quality (1-100, default: 60)
    - format: Screenshot format 'jpeg' or 'png' (default: 'jpeg')
    - log_enabled: Enable/disable logging (true/false, default: true)
    - log_requests: Log HTTP requests/responses (true/false, default: true)
    - log_cookies: Log cookies (true/false, default: true)
    - log_localstorage: Log localStorage (true/false, default: true)
    - log_sessionstorage: Log sessionStorage (true/false, default: true)
    - log_console: Log console messages (true/false, default: true)
    - log_interactions: Log user interactions (true/false, default: true)
    - log_response_body: Log response bodies (true/false, default: false)
    - log_request_body: Log request bodies (true/false, default: false)
    """
    # Use query params or defaults
    stream_fps = fps if fps is not None else CONFIG['streaming_fps']
    stream_width = width if width is not None else CONFIG['viewport_width']
    stream_height = height if height is not None else CONFIG['viewport_height']
    stream_quality = quality if quality is not None else CONFIG['jpeg_quality']
    stream_format = (format or CONFIG['screenshot_format']).lower()
    
    # Update logging configuration if provided
    if browser_manager and browser_manager.initialized:
        log_params = {}
        if log_enabled is not None:
            log_params['log_enabled'] = log_enabled
        if log_requests is not None:
            log_params['log_requests'] = log_requests
        if log_cookies is not None:
            log_params['log_cookies'] = log_cookies
        if log_localstorage is not None:
            log_params['log_localstorage'] = log_localstorage
        if log_sessionstorage is not None:
            log_params['log_sessionstorage'] = log_sessionstorage
        if log_console is not None:
            log_params['log_console'] = log_console
        if log_interactions is not None:
            log_params['log_interactions'] = log_interactions
        if log_response_body is not None:
            log_params['log_response_body'] = log_response_body
        if log_request_body is not None:
            log_params['log_request_body'] = log_request_body
        
        if log_params:
            browser_manager.set_logging_config(**log_params)
            # Re-setup logging with new config
            browser_manager._setup_logging()
    
    # Navigate to URL if browser is ready
    if browser_manager and browser_manager.initialized:
        try:
            await browser_manager.navigate(url)
            await asyncio.sleep(0.5)  # Wait a bit for page to load
        except Exception as e:
            if CONFIG['debug']:
                print(f"⚠️  Auto-navigation error: {e}")
    
    # Return HTML with stream
    html_content = get_stream_html(
        fps=stream_fps,
        width=stream_width,
        height=stream_height,
        quality=stream_quality,
        format=stream_format,
        url=url
    )
    return HTMLResponse(content=html_content)

def get_index_html():
    """Get main HTML interface with WebSocket streaming"""
    # This will be replaced with React frontend later
    # For now, return the HTML from the original implementation
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MITM Browser - Real-time Stream</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a1a; color: #e0e0e0; height: 100vh;
            display: flex; flex-direction: column; overflow: hidden;
        }
        .browser-container {
            flex: 1; display: flex; flex-direction: column;
            background: #0a0a0a; overflow: hidden; position: relative;
        }
        .browser-frame {
            flex: 1; display: flex; align-items: center; justify-content: center;
            background: #000; position: relative; overflow: auto; cursor: pointer;
        }
        .browser-stream {
            max-width: 100%; max-height: 100%;
            box-shadow: 0 4px 20px rgba(0,0,0,0.8); border: 2px solid #333;
            user-select: none; image-rendering: crisp-edges;
        }
        .loading { color: #888; font-size: 18px; text-align: center; }
        .error { color: #f44336; padding: 20px; text-align: center; }
    </style>
</head>
<body>
    <div class="browser-container">
        <div class="browser-frame" id="browserFrame">
            <div class="loading">Connecting to stream...</div>
        </div>
    </div>
    <script>
        let ws = null; let streamImg = null; let isInteracting = false;
        // Use same port as HTTP (FastAPI serves WebSocket on /ws endpoint)
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws`;
        
        function connectWebSocket() {
            ws = new WebSocket(wsUrl);
            ws.onopen = () => {
                document.getElementById('browserFrame').innerHTML = '<div class="loading">Waiting for stream...</div>';
                const defaultUrl = '{{DEFAULT_URL}}';
                if (defaultUrl && defaultUrl.trim() !== '') {
                    setTimeout(() => navigateToUrl(defaultUrl), 1000);
                }
            };
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'init') {
                    const defaultUrl = '{{DEFAULT_URL}}';
                    if (defaultUrl && defaultUrl.trim() !== '') {
                        setTimeout(() => navigateToUrl(defaultUrl), 500);
                    }
                } else if (data.f) {
                    updateFrame(data);
                } else if (data.type === 'navigate_result' && !data.success) {
                    alert('Navigation failed');
                }
            };
            ws.onerror = (error) => console.error('WebSocket error:', error);
            ws.onclose = () => {
                streamImg = null;
                document.getElementById('browserFrame').innerHTML = '<div class="loading">Reconnecting...</div>';
                setTimeout(connectWebSocket, 2000);
            };
        }
        
        function updateFrame(data) {
            const frame = document.getElementById('browserFrame');
            if (!streamImg) {
                streamImg = document.createElement('img');
                streamImg.className = 'browser-stream';
                frame.innerHTML = '';
                frame.appendChild(streamImg);
                streamImg.addEventListener('click', handleClick);
                streamImg.addEventListener('contextmenu', (e) => { e.preventDefault(); handleClick(e, true); });
            }
            const format = data.fmt || 'jpeg';
            streamImg.src = 'data:image/' + format + ';base64,' + data.f;
        }
        
        async function handleClick(event, rightClick = false) {
            if (isInteracting || !streamImg || !ws) return;
            isInteracting = true;
            const img = event.target;
            const rect = img.getBoundingClientRect();
            const x = (event.clientX - rect.left) * (1024 / rect.width);
            const y = (event.clientY - rect.top) * (768 / rect.height);
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'click', x: x, y: y }));
            }
            setTimeout(() => { isInteracting = false; }, 100);
        }
        
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT') return;
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            if (e.key === 'Enter') { e.preventDefault(); ws.send(JSON.stringify({ action: 'key', key: 'Enter' })); }
            else if (e.key === 'Escape') { e.preventDefault(); ws.send(JSON.stringify({ action: 'key', key: 'Escape' })); }
            else if (e.key === 'Backspace') { e.preventDefault(); ws.send(JSON.stringify({ action: 'key', key: 'Backspace' })); }
            else if (e.key === 'Tab') { e.preventDefault(); ws.send(JSON.stringify({ action: 'key', key: 'Tab' })); }
            else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
                e.preventDefault();
                ws.send(JSON.stringify({ action: 'type', text: e.key }));
            }
        });
        
        document.getElementById('browserFrame').addEventListener('wheel', (e) => {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            e.preventDefault();
            ws.send(JSON.stringify({ action: 'scroll', deltaX: e.deltaX, deltaY: e.deltaY }));
        });
        
        function navigateToUrl(url) {
            if (!url || !ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ action: 'navigate', url: url }));
        }
        
        connectWebSocket();
    </script>
</body>
</html>"""
    return html_template


def get_stream_html(fps: int = 15, width: int = None, height: int = None, quality: int = None, format: str = None, url: str = None, mode: str = "full"):
    """Get HTML page for /stream with MJPEG stream
    
    Args:
        mode: 'full' for interactive controls, 'view-only' for non-interactive view
    """
    viewport_width = width if width is not None else CONFIG['viewport_width']
    viewport_height = height if height is not None else CONFIG['viewport_height']
    default_url = url if url else ''
    
    # Build stream URL with query parameters (always use view-only for MJPEG)
    stream_params = [f'mode=view-only', f'fps={fps}']
    if width is not None:
        stream_params.append(f'width={width}')
    if height is not None:
        stream_params.append(f'height={height}')
    if quality is not None:
        stream_params.append(f'quality={quality}')
    if format is not None:
        stream_params.append(f'format={format}')
    if url:
        # URL encode the url parameter
        stream_params.append(f'url={urllib.parse.quote(url, safe="")}')
    stream_url = '/stream?' + '&'.join(stream_params)
    
    # For view-only mode, return non-interactive HTML
    if mode == "view-only":
        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MITM Browser - View Only</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{
            width: 100%; height: 100%; overflow: hidden;
            background: #000; cursor: none !important;
        }}
        .browser-container {{
            width: 100%; height: 100%; display: flex;
            align-items: center; justify-content: center;
            background: #000; position: relative;
            pointer-events: none !important; /* Disable all interactions */
            user-select: none !important;
            -webkit-user-select: none !important;
            -moz-user-select: none !important;
            -ms-user-select: none !important;
        }}
        .browser-stream {{
            max-width: 100%; max-height: 100%;
            pointer-events: none !important; /* Disable clicks */
            cursor: none !important;
            user-select: none !important;
            -webkit-user-select: none !important;
            -moz-user-select: none !important;
            -ms-user-select: none !important;
        }}
        .loading {{
            color: #888; font-size: 18px; text-align: center;
            pointer-events: none !important;
        }}
    </style>
</head>
<body>
    <div class="browser-container">
        <img src="{stream_url}" class="browser-stream" alt="Browser Stream" />
    </div>
    <script>
        // Disable all interactions
        document.addEventListener('click', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('contextmenu', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('mousedown', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('mouseup', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('keydown', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('keyup', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('wheel', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('touchstart', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('touchmove', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        document.addEventListener('touchend', (e) => {{ e.preventDefault(); e.stopPropagation(); }}, true);
        
        // Hide cursor
        document.body.style.cursor = 'none';
        
        // Prevent text selection
        document.onselectstart = function() {{ return false; }};
        document.ondragstart = function() {{ return false; }};
        
        // Prevent navigation
        window.addEventListener('beforeunload', (e) => {{ e.preventDefault(); }});
    </script>
</body>
</html>"""
        return html_template
    
    # For full mode, return interactive HTML with WebSocket controls
    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MITM Browser - Stream (Full Control)</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a1a; color: #e0e0e0; height: 100vh;
            display: flex; flex-direction: column; overflow: hidden;
        }}
        .browser-container {{
            flex: 1; display: flex; flex-direction: column;
            background: #0a0a0a; overflow: hidden; position: relative;
        }}
        .browser-frame {{
            flex: 1; display: flex; align-items: center; justify-content: center;
            background: #000; position: relative; overflow: auto; cursor: pointer;
        }}
        .browser-stream {{
            max-width: 100%; max-height: 100%;
            box-shadow: 0 4px 20px rgba(0,0,0,0.8); border: 2px solid #333;
            user-select: none; image-rendering: crisp-edges;
        }}
        .loading {{ color: #888; font-size: 18px; text-align: center; }}
        .error {{ color: #f44336; padding: 20px; text-align: center; }}
    </style>
</head>
<body>
    <div class="browser-container">
        <div class="browser-frame" id="browserFrame">
            <div class="loading">Loading stream...</div>
        </div>
    </div>
    <script>
        let ws = null;
        let streamImg = null;
        let isInteracting = false;
        const viewportWidth = {viewport_width};
        const viewportHeight = {viewport_height};
        const streamUrl = '{stream_url}';
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${{wsProtocol}}//${{window.location.host}}/ws`;
        
        // Connect WebSocket for controls
        function connectWebSocket() {{
            ws = new WebSocket(wsUrl);
            ws.onopen = () => {{
                const defaultUrl = '{default_url}';
                if (defaultUrl && defaultUrl.trim() !== '') {{
                    setTimeout(() => navigateToUrl(defaultUrl), 1000);
                }}
            }};
            ws.onmessage = (event) => {{
                const data = JSON.parse(event.data);
                if (data.type === 'init') {{
                    const defaultUrl = '{default_url}';
                    if (defaultUrl && defaultUrl.trim() !== '') {{
                        setTimeout(() => navigateToUrl(defaultUrl), 500);
                    }}
                }} else if (data.type === 'navigate_result') {{
                    if (!data.success) {{
                        alert('Navigation failed');
                    }}
                }}
            }};
            ws.onerror = (error) => {{
                console.error('WebSocket error:', error);
            }};
            ws.onclose = () => {{
                // Reconnect after 2 seconds
                setTimeout(connectWebSocket, 2000);
            }};
        }}
        
        // Load MJPEG stream
        function loadStream() {{
            const frame = document.getElementById('browserFrame');
            if (!streamImg) {{
                streamImg = document.createElement('img');
                streamImg.className = 'browser-stream';
                streamImg.alt = 'Browser stream';
                frame.innerHTML = '';
                frame.appendChild(streamImg);
                
                streamImg.addEventListener('click', handleClick);
                streamImg.addEventListener('contextmenu', (e) => {{
                    e.preventDefault();
                    handleClick(e, true);
                }});
            }}
            streamImg.src = streamUrl;
        }}
        
        async function handleClick(event, rightClick = false) {{
            if (isInteracting || !streamImg || !ws) return;
            isInteracting = true;
            
            const img = event.target;
            const rect = img.getBoundingClientRect();
            const x = ((event.clientX - rect.left) * viewportWidth) / rect.width;
            const y = ((event.clientY - rect.top) * viewportHeight) / rect.height;
            
            if (ws.readyState === WebSocket.OPEN) {{
                ws.send(JSON.stringify({{ action: 'click', x: x, y: y }}));
            }}
            
            setTimeout(() => {{ isInteracting = false; }}, 100);
        }}
        
        document.addEventListener('keydown', (e) => {{
            if (e.target.tagName === 'INPUT') return;
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            
            if (e.key === 'Enter') {{
                e.preventDefault();
                ws.send(JSON.stringify({{ action: 'key', key: 'Enter' }}));
            }} else if (e.key === 'Escape') {{
                e.preventDefault();
                ws.send(JSON.stringify({{ action: 'key', key: 'Escape' }}));
            }} else if (e.key === 'Backspace') {{
                e.preventDefault();
                ws.send(JSON.stringify({{ action: 'key', key: 'Backspace' }}));
            }} else if (e.key === 'Tab') {{
                e.preventDefault();
                ws.send(JSON.stringify({{ action: 'key', key: 'Tab' }}));
            }} else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {{
                e.preventDefault();
                ws.send(JSON.stringify({{ action: 'type', text: e.key }}));
            }}
        }});
        
        document.getElementById('browserFrame').addEventListener('wheel', (e) => {{
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            e.preventDefault();
            ws.send(JSON.stringify({{ action: 'scroll', deltaX: e.deltaX, deltaY: e.deltaY }}));
        }});
        
        function navigateToUrl(url) {{
            if (!url || !ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({{ action: 'navigate', url: url }}));
        }}
        
        // Initialize
        connectWebSocket();
        loadStream();
    </script>
</body>
</html>"""
    return html_template

