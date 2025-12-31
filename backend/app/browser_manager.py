#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Browser Manager - Manages browser instance and streaming
"""

import asyncio
import json
import base64
import time
from urllib.parse import urlparse, parse_qs

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None
    BrowserContext = None
    Page = None

from .config import CONFIG

# activity_logger will be set after initialization
activity_logger = None

def set_activity_logger(logger):
    """Set activity logger instance"""
    global activity_logger
    activity_logger = logger

class BrowserManager:
    """Manages browser instance and streaming"""
    
    def __init__(self):
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.playwright = None
        self.current_url = ""
        self.initialized = False
        self.init_error = None
        self.viewport_width = CONFIG['viewport_width']
        self.viewport_height = CONFIG['viewport_height']
        self.screenshot_format = CONFIG['screenshot_format']
        self.jpeg_quality = CONFIG['jpeg_quality']
        # Logging configuration (can be overridden via URL params)
        self.log_config = {
            'log_enabled': CONFIG['log_enabled'],
            'log_requests': CONFIG['log_requests'],
            'log_cookies': CONFIG['log_cookies'],
            'log_localstorage': CONFIG['log_localstorage'],
            'log_sessionstorage': CONFIG['log_sessionstorage'],
            'log_console': CONFIG['log_console'],
            'log_interactions': CONFIG['log_interactions'],
            'log_response_body': CONFIG['log_response_body'],
            'log_request_body': CONFIG['log_request_body'],
        }
        
    async def initialize(self):
        """Initialize browser"""
        if not PLAYWRIGHT_AVAILABLE:
            self.init_error = "Playwright not available"
            return False
        
        try:
            print("Starting Playwright...")
            self.playwright = await async_playwright().start()
            print("Launching browser...")
            # Launch browser with anti-detection args
            browser_args = CONFIG['browser_args'].copy() if isinstance(CONFIG['browser_args'], list) else CONFIG['browser_args'].split()
            
            # Add anti-detection flags
            anti_detection_args = [
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--start-maximized'
            ]
            
            # Merge with user args (avoid duplicates)
            for arg in anti_detection_args:
                if arg not in browser_args:
                    browser_args.append(arg)
            
            self.browser = await self.playwright.chromium.launch(
                headless=CONFIG['headless'],
                args=browser_args
            )
            
            # Use realistic user agent if not provided
            user_agent = CONFIG['user_agent'] if CONFIG['user_agent'] else \
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            
            self.context = await self.browser.new_context(
                viewport={'width': self.viewport_width, 'height': self.viewport_height},
                user_agent=user_agent,
                locale='en-US',
                timezone_id='America/New_York',
                permissions=['geolocation', 'notifications'],
                extra_http_headers={
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            self.page = await self.context.new_page()
            
            # Setup logging listeners (before navigation)
            self._setup_logging()
            
            # Inject anti-detection JavaScript BEFORE any other scripts
            await self.page.add_init_script("""
                (function() {
                    // Remove webdriver flag
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    
                    // Override plugins
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    
                    // Override languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en']
                    });
                    
                    // Override permissions
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );
                    
                    // Chrome runtime
                    window.chrome = {
                        runtime: {}
                    };
                    
                    // Override toString methods
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) {
                            return 'Intel Inc.';
                        }
                        if (parameter === 37446) {
                            return 'Intel Iris OpenGL Engine';
                        }
                        return getParameter.call(this, parameter);
                    };
                    
                    // Override canvas fingerprinting
                    const toBlob = HTMLCanvasElement.prototype.toBlob;
                    const toDataURL = HTMLCanvasElement.prototype.toDataURL;
                    const getImageData = CanvasRenderingContext2D.prototype.getImageData;
                    
                    // Add noise to canvas
                    const getContext = HTMLCanvasElement.prototype.getContext;
                    HTMLCanvasElement.prototype.getContext = function(type) {
                        const context = getContext.apply(this, arguments);
                        if (type === '2d') {
                            const originalFillText = context.fillText;
                            context.fillText = function() {
                                const result = originalFillText.apply(this, arguments);
                                const imageData = this.getImageData(0, 0, this.canvas.width, this.canvas.height);
                                for (let i = 0; i < imageData.data.length; i += 4) {
                                    imageData.data[i] += Math.random() * 0.01;
                                }
                                this.putImageData(imageData, 0, 0);
                                return result;
                            };
                        }
                        return context;
                    };
                })();
            """)
            
            # Use CDP to remove automation indicators
            try:
                cdp_session = await self.context.new_cdp_session(self.page)
                await cdp_session.send('Page.addScriptToEvaluateOnNewDocument', {
                    'source': '''
                        Object.defineProperty(navigator, "webdriver", {get: () => undefined});
                        delete navigator.__proto__.webdriver;
                    '''
                })
                await cdp_session.send('Network.setUserAgentOverride', {
                    'userAgent': user_agent,
                    'acceptLanguage': 'en-US,en;q=0.9',
                    'platform': 'Win32'
                })
            except Exception as e:
                if CONFIG['debug']:
                    print(f"⚠️  CDP session error (non-critical): {e}")
            
            # Inject JavaScript to intercept fetch and XMLHttpRequest for request body capture
            await self.page.add_init_script("""
                (function() {
                    // Intercept fetch
                    const originalFetch = window.fetch;
                    window.fetch = function(...args) {
                        const url = args[0];
                        const options = args[1] || {};
                        const method = options.method || 'GET';
                        const body = options.body;
                        
                        // Log request body if it exists
                        if (body && (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE')) {
                            try {
                                const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
                                if (window.__mitm_log_request_body) {
                                    window.__mitm_log_request_body(url, method, bodyStr);
                                }
                            } catch(e) {
                                console.error('Error logging fetch body:', e);
                            }
                        }
                        
                        return originalFetch.apply(this, args);
                    };
                    
                    // Intercept XMLHttpRequest
                    const originalOpen = XMLHttpRequest.prototype.open;
                    const originalSend = XMLHttpRequest.prototype.send;
                    
                    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                        this._mitm_method = method;
                        this._mitm_url = url;
                        return originalOpen.apply(this, [method, url, ...rest]);
                    };
                    
                    XMLHttpRequest.prototype.send = function(body) {
                        if (body && (this._mitm_method === 'POST' || this._mitm_method === 'PUT' || this._mitm_method === 'PATCH' || this._mitm_method === 'DELETE')) {
                            try {
                                const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
                                if (window.__mitm_log_request_body) {
                                    window.__mitm_log_request_body(this._mitm_url, this._mitm_method, bodyStr);
                                }
                            } catch(e) {
                                console.error('Error logging XHR body:', e);
                            }
                        }
                        return originalSend.apply(this, [body]);
                    };
                })();
            """)
            
            # Expose function to log request body from JavaScript (only if enabled)
            if self.log_config['log_request_body']:
                async def log_request_body_from_js(url, method, body_str):
                    """Log request body captured from JavaScript interception"""
                    try:
                        # Parse JSON if possible
                        body_data = body_str
                        body_type = 'text'
                        try:
                            body_json = json.loads(body_str)
                            body_data = body_json
                            body_type = 'json'
                        except:
                            pass
                        
                        # Store in a special map for JavaScript-captured bodies
                        if not hasattr(self, '_js_body_map'):
                            self._js_body_map = {}
                        
                        # Use URL + method as key (since we don't have request ID from JS)
                        key = f"{method}:{url}"
                        self._js_body_map[key] = {
                            'body': body_data,
                            'body_type': body_type,
                            'timestamp': time.time()
                        }
                        
                    except Exception as e:
                        pass
                
                await self.page.expose_function('__mitm_log_request_body', log_request_body_from_js)
            
            await self.page.goto('about:blank', wait_until='domcontentloaded', timeout=10000)
            
            # Log initial cookies if enabled
            if self.log_config['log_cookies'] and activity_logger:
                cookies = await self.get_cookies()
                if cookies:
                    activity_logger.log_cookies(cookies, action='read')
            
            self.initialized = True
            return True
        except Exception as e:
            self.init_error = str(e)
            print(f"❌ Browser initialization error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def navigate(self, url: str):
        """Navigate to URL"""
        if not self.page:
            return False
        
        try:
            if not url.startswith(('http://', 'https://', 'about:')):
                url = 'http://' + url
            
            # Log navigation
            if self.log_config['log_interactions'] and activity_logger:
                activity_logger.log_interaction('navigate', {'url': url})
            
            await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
            self.current_url = self.page.url
            
            # Wait a bit for page to fully load
            await asyncio.sleep(0.5)
            
            # Re-inject storage monitoring after navigation (in case page overwrote it)
            if self.log_config['log_localstorage'] or self.log_config['log_sessionstorage']:
                try:
                    await self.page.evaluate("""
                        (function() {
                            function setupStorageMonitoring() {
                                // Monitor localStorage
                                if (window.localStorage && window.__mitm_log_storage) {
                                    const originalSetItem = localStorage.setItem;
                                    const originalRemoveItem = localStorage.removeItem;
                                    const originalClear = localStorage.clear;
                                    
                                    localStorage.setItem = function(key, value) {
                                        try {
                                            window.__mitm_log_storage('localStorage', key, value, 'set').catch(() => {});
                                        } catch(e) {}
                                        return originalSetItem.apply(this, arguments);
                                    };
                                    
                                    localStorage.removeItem = function(key) {
                                        try {
                                            window.__mitm_log_storage('localStorage', key, null, 'remove').catch(() => {});
                                        } catch(e) {}
                                        return originalRemoveItem.apply(this, arguments);
                                    };
                                    
                                    localStorage.clear = function() {
                                        try {
                                            window.__mitm_log_storage('localStorage', null, null, 'clear').catch(() => {});
                                        } catch(e) {}
                                        return originalClear.apply(this, arguments);
                                    };
                                }
                                
                                // Monitor sessionStorage
                                if (window.sessionStorage && window.__mitm_log_storage) {
                                    const originalSetItem = sessionStorage.setItem;
                                    const originalRemoveItem = sessionStorage.removeItem;
                                    const originalClear = sessionStorage.clear;
                                    
                                    sessionStorage.setItem = function(key, value) {
                                        try {
                                            window.__mitm_log_storage('sessionStorage', key, value, 'set').catch(() => {});
                                        } catch(e) {}
                                        return originalSetItem.apply(this, arguments);
                                    };
                                    
                                    sessionStorage.removeItem = function(key) {
                                        try {
                                            window.__mitm_log_storage('sessionStorage', key, null, 'remove').catch(() => {});
                                        } catch(e) {}
                                        return originalRemoveItem.apply(this, arguments);
                                    };
                                    
                                    sessionStorage.clear = function() {
                                        try {
                                            window.__mitm_log_storage('sessionStorage', null, null, 'clear').catch(() => {});
                                        } catch(e) {}
                                        return originalClear.apply(this, arguments);
                                    };
                                }
                            }
                            setupStorageMonitoring();
                        })();
                    """)
                except Exception as e:
                    print(f"⚠️  Error re-injecting storage monitoring: {e}")
            
            # Log cookies after navigation
            if self.log_config['log_cookies'] and activity_logger:
                try:
                    cookies = await self.get_cookies()
                    if cookies:
                        activity_logger.log_cookies(cookies, action='read')
                except Exception as e:
                    if CONFIG['debug']:
                        print(f"⚠️  Error logging cookies: {e}")
            
            # Log initial storage state
            if self.log_config['log_localstorage'] and activity_logger:
                try:
                    local_storage = await self.get_local_storage()
                    if local_storage:
                        for key, value in local_storage.items():
                            activity_logger.log_storage('localStorage', key, value, 'get')
                except Exception as e:
                    if CONFIG['debug']:
                        print(f"⚠️  Error logging localStorage: {e}")
            
            if self.log_config['log_sessionstorage'] and activity_logger:
                try:
                    session_storage = await self.get_session_storage()
                    if session_storage:
                        for key, value in session_storage.items():
                            activity_logger.log_storage('sessionStorage', key, value, 'get')
                except Exception as e:
                    if CONFIG['debug']:
                        print(f"⚠️  Error logging sessionStorage: {e}")
            
            return True
        except Exception as e:
            print(f"❌ Navigation error: {e}")
            return False
    
    async def click(self, x: float, y: float):
        """Click at coordinates"""
        if not self.page:
            return False
        
        try:
            # Get element info at click position (before click)
            element_info = None
            if self.log_config['log_interactions']:
                try:
                    element_info = await self.page.evaluate(f"""
                        (() => {{
                            const element = document.elementFromPoint({x}, {y});
                            if (!element) return null;
                            
                            return {{
                                tag: element.tagName?.toLowerCase() || '',
                                id: element.id || '',
                                name: element.name || '',
                                type: element.type || '',
                                className: element.className || '',
                                text: element.textContent?.trim().substring(0, 50) || '',
                                href: element.href || '',
                                value: element.value || '',
                                selector: element.id ? '#' + element.id : 
                                          element.className ? '.' + element.className.split(' ')[0] : 
                                          element.tagName?.toLowerCase() || ''
                            }};
                        }})()
                    """)
                except Exception as e:
                    print(f"⚠️  Error getting element info: {e}")
            
            # Log click with element info
            if self.log_config['log_interactions'] and activity_logger:
                activity_logger.log_interaction('click', {
                    'x': x, 
                    'y': y,
                    'element': element_info
                })
            
            await self.page.mouse.click(x, y)
            return True
        except Exception as e:
            print(f"❌ Click error: {e}")
            return False
    
    async def type_text(self, text: str):
        """Type text"""
        if not self.page:
            return False
        
        try:
            # Get active element info
            element_info = None
            if self.log_config['log_interactions']:
                try:
                    element_info = await self.page.evaluate("""
                        (() => {
                            const activeElement = document.activeElement;
                            if (!activeElement) return null;
                            
                            return {
                                tag: activeElement.tagName?.toLowerCase() || '',
                                id: activeElement.id || '',
                                name: activeElement.name || '',
                                type: activeElement.type || '',
                                className: activeElement.className || '',
                                placeholder: activeElement.placeholder || '',
                                isPassword: activeElement.type === 'password',
                                isEmail: activeElement.type === 'email' || activeElement.name?.toLowerCase().includes('email'),
                                selector: activeElement.id ? '#' + activeElement.id : 
                                          activeElement.className ? '.' + activeElement.className.split(' ')[0] : 
                                          activeElement.tagName?.toLowerCase() || ''
                            };
                        })()
                    """)
                except:
                    pass
            
            # Log typing with element info
            if self.log_config['log_interactions']:
                # Mask sensitive data (password fields)
                is_sensitive = element_info and (
                    element_info.get('isPassword') or 
                    element_info.get('type') == 'password' or
                    'password' in (element_info.get('name') or '').lower() or
                    'password' in (element_info.get('id') or '').lower()
                )
                
                if is_sensitive:
                    # For password fields: save only "*" as text, but keep the actual length
                    masked_text = '*'
                else:
                    # For non-password fields: save actual text (truncated if too long)
                    masked_text = text[:100] + '...' if len(text) > 100 else text
                
                if activity_logger:
                    activity_logger.log_interaction('type', {
                        'text': masked_text, 
                        'length': len(text),
                        'element': element_info,
                        'isSensitive': is_sensitive
                    })
            
            await self.page.keyboard.type(text, delay=CONFIG['typing_delay'])
            return True
        except Exception as e:
            print(f"❌ Type error: {e}")
            return False
    
    async def press_key(self, key: str):
        """Press a key"""
        if not self.page:
            return False
        
        try:
            await self.page.keyboard.press(key)
            return True
        except Exception as e:
            print(f"❌ Key press error: {e}")
            return False
    
    async def scroll(self, delta_x: float, delta_y: float):
        """Scroll page"""
        if not self.page:
            return False
        
        try:
            # Log scroll (only significant scrolls)
            if self.log_config['log_interactions'] and activity_logger and abs(delta_y) > 50:
                activity_logger.log_interaction('scroll', {'delta_x': delta_x, 'delta_y': delta_y})
            
            await self.page.mouse.wheel(delta_x, delta_y)
            return True
        except Exception as e:
            print(f"❌ Scroll error: {e}")
            return False
    
    async def get_screenshot(self):
        """Get screenshot as base64 (optimized for speed) or HTML code if page is HTML"""
        if not self.page:
            return None
        
        # Check if page/context/browser is closed before attempting screenshot
        try:
            # Check if page is closed
            if self.page.is_closed():
                return None
        except:
            # If check fails, page might be closed
            return None
        
        # Check if context/browser is still connected
        try:
            if self.context and hasattr(self.context, 'browser'):
                if self.context.browser and not self.context.browser.is_connected():
                    return None
        except:
            # If check fails, context/browser might be closed
            return None
        
        try:
            # Use optimized screenshot settings for maximum speed
            screenshot = await self.page.screenshot(
                full_page=False,
                type=self.screenshot_format,
                quality=self.jpeg_quality if self.screenshot_format == 'jpeg' else None,
                timeout=2000,  # 2 second timeout (faster)
                omit_background=False  # Keep background for better quality
            )
            if screenshot:
                # Use faster base64 encoding
                return base64.b64encode(screenshot).decode('utf-8')
        except Exception as e:
            error_msg = str(e).lower()
            # Only log if it's not a "closed" error (these are expected during shutdown)
            if "closed" not in error_msg and CONFIG['debug']:
                print(f"⚠️  Screenshot error: {e}")
        return None

    async def get_screenshot_bytes(self, fmt: str | None = None, quality: int | None = None) -> bytes | None:
        """
        Get screenshot as raw bytes (for HTTP streaming endpoints like MJPEG).
        If fmt is provided, it overrides configured screenshot_format for this call.
        If quality is provided, it overrides configured jpeg_quality for this call.
        """
        if not self.page:
            return None

        # Check if page/context/browser is closed before attempting screenshot
        try:
            if self.page.is_closed():
                return None
        except Exception:
            return None

        try:
            if self.context and hasattr(self.context, "browser"):
                if self.context.browser and not self.context.browser.is_connected():
                    return None
        except Exception:
            return None

        screenshot_format = (fmt or self.screenshot_format or "jpeg").lower()
        jpeg_quality = quality if quality is not None else self.jpeg_quality
        try:
            screenshot = await self.page.screenshot(
                full_page=False,
                type=screenshot_format,
                quality=jpeg_quality if screenshot_format == "jpeg" else None,
                timeout=2000,
                omit_background=False,
            )
            return screenshot if screenshot else None
        except Exception as e:
            error_msg = str(e).lower()
            if "closed" not in error_msg and CONFIG['debug']:
                print(f"⚠️  Screenshot error: {e}")
            return None
    
    async def get_page_info(self):
        """Get current page information"""
        if not self.initialized:
            return {
                'url': '',
                'title': '',
                'ready': False,
                'error': self.init_error or 'Not initialized'
            }
        
        if not self.page:
            return {
                'url': '',
                'title': '',
                'ready': False,
                'error': 'No page available'
            }
        
        try:
            title = await self.page.title()
            return {
                'url': self.page.url or 'about:blank',
                'title': title,
                'ready': True
            }
        except Exception as e:
            return {
                'url': '',
                'title': '',
                'ready': False,
                'error': str(e)
            }
    
    def _setup_logging(self):
        """Setup event listeners for logging"""
        if not self.log_config['log_enabled']:
            print("⚠️ Logging is disabled (log_enabled=false)")
            return
        
        if not activity_logger:
            print("⚠️ [ERROR] activity_logger is None, cannot setup logging")
            return
        
        if not activity_logger.enabled:
            print("⚠️ [ERROR] activity_logger is disabled, cannot setup logging")
            return
        
        if not self.page:
            print("⚠️ [ERROR] Page is None, cannot setup logging")
            return
        
        page = self.page
        
        # Store request data for matching with responses (instance variable)
        if not hasattr(self, '_request_data_map'):
            self._request_data_map = {}
        
        # Log network requests
        if self.log_config['log_requests']:
            # Use route handler to intercept and capture body - this is the most reliable method
            async def handle_route(route):
                request = route.request
                request_id = id(request)
                
                # Extract query parameters (for ALL request types)
                query_params = {}
                try:
                    parsed_url = urlparse(request.url)
                    if parsed_url.query:
                        query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_url.query).items()}
                except:
                    pass
                
                # Get request body - In route handler, post_data is available synchronously
                body_data = None
                body_type = None
                
                # Try to get body for POST/PUT/PATCH/DELETE requests (GET requests don't have body)
                if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                    # Method 1: Try post_data first (synchronous, available in route handler)
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
                    else:
                        # Method 2: Try post_data_buffer (property, not method)
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
                            else:
                                body_data = ''
                                body_type = 'empty'
                        except Exception as e:
                            body_data = ''
                            body_type = 'empty'
                
                # Parse JSON if possible
                if body_data and body_type == 'text' and body_data != '<binary>' and body_data != '':
                    try:
                        body_json = json.loads(body_data)
                        body_data = body_json
                        body_type = 'json'
                    except:
                        # Not JSON, keep as text
                        pass
                
                # Store request data (always store body, even if None/empty)
                request_data = {
                    'url': request.url,
                    'method': request.method,
                    'headers': dict(request.headers),
                    'query_params': query_params,
                    'body': body_data,  # Can be None, '', object (JSON), string, or '<binary>'
                    'body_type': body_type,  # Can be None, 'text', 'json', 'binary', 'empty'
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                }
                
                self._request_data_map[request_id] = request_data
                
                # Continue the request (don't block it)
                await route.continue_()
            
            # Setup route handler to intercept ALL requests BEFORE they're sent
            page.route('**/*', handle_route)
            
            # Also use page.on('request') as backup to capture body if route handler missed it
            async def handle_request(request):
                request_id = id(request)
                
                # Only capture if not already captured by route handler
                if request_id not in self._request_data_map:
                    # Extract query parameters
                    query_params = {}
                    try:
                        parsed_url = urlparse(request.url)
                        if parsed_url.query:
                            query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_url.query).items()}
                    except:
                        pass
                    
                    # Get request body
                    body_data = None
                    body_type = None
                    
                    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                        try:
                            # Try post_data first
                            if request.post_data:
                                post_data = request.post_data
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
                            # Try post_data_buffer (property)
                            elif hasattr(request, 'post_data_buffer') and request.post_data_buffer:
                                post_buffer = request.post_data_buffer
                                if isinstance(post_buffer, bytes):
                                    try:
                                        body_str = post_buffer.decode('utf-8', errors='ignore')
                                        body_data = body_str
                                        body_type = 'text'
                                    except:
                                        import base64
                                        body_data = base64.b64encode(post_buffer).decode('ascii')
                                        body_type = 'binary'
                                else:
                                    body_data = str(post_buffer) if post_buffer else ''
                                    body_type = 'text' if post_buffer else 'empty'
                            else:
                                body_data = ''
                                body_type = 'empty'
                        except Exception as e:
                            if CONFIG['debug']:
                                print(f"⚠️  Error getting request body in handle_request: {e}")
                            body_data = ''
                            body_type = 'empty'
                    
                    # Parse JSON if possible
                    if body_data and body_type == 'text' and body_data != '<binary>' and body_data != '':
                        try:
                            body_json = json.loads(body_data)
                            body_data = body_json
                            body_type = 'json'
                        except:
                            pass
                    
                    request_data = {
                        'url': request.url,
                        'method': request.method,
                        'headers': dict(request.headers),
                        'query_params': query_params,
                        'body': body_data,
                        'body_type': body_type,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                    }
                    
                    self._request_data_map[request_id] = request_data
            
            # Setup request handler as backup
            page.on('request', handle_request)
            
            # Handle responses and merge with request data
            async def handle_response(response):
                # Check if logging is enabled
                if not self.log_config['log_requests']:
                    return
                
                request = response.request
                request_id = id(request)
                
                request_data = self._request_data_map.get(request_id, {})
                
                # If request_data is empty, create it from response.request
                if not request_data:
                    # Extract query parameters
                    query_params = {}
                    try:
                        parsed_url = urlparse(request.url)
                        if parsed_url.query:
                            query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed_url.query).items()}
                    except:
                        pass
                    
                    request_data = {
                        'url': request.url,
                        'method': request.method,
                        'headers': dict(request.headers),
                        'query_params': query_params,
                        'body': None,
                        'body_type': None,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                    }
                
                # Get response body (only if enabled)
                response_body = None
                response_body_type = None
                
                if self.log_config['log_response_body']:
                    try:
                        body = await response.body()
                        if body:
                            try:
                                body_str = body.decode('utf-8', errors='ignore')
                                # Try to parse as JSON
                                try:
                                    body_json = json.loads(body_str)
                                    response_body = body_json
                                    response_body_type = 'json'
                                except:
                                    response_body = body_str[:10000]  # Limit size
                                    response_body_type = 'text'
                            except:
                                # Binary data - encode as base64 for display
                                response_body = base64.b64encode(body).decode('ascii')
                                response_body_type = 'binary'
                    except:
                        response_body = ''
                        response_body_type = 'empty'
                else:
                    # If response body logging is disabled, set empty
                    response_body = ''
                    response_body_type = 'empty'
                
                # Get request body - Try multiple sources
                req_body = request_data.get('body')
                req_body_type = request_data.get('body_type')
                
                # Method 1: Try JavaScript-captured body (most reliable for fetch/XHR)
                if (req_body is None or req_body == '') and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                    if hasattr(self, '_js_body_map'):
                        key = f"{request.method}:{request.url}"
                        js_body_data = self._js_body_map.get(key)
                        if js_body_data:
                            # Check if timestamp is recent (within last 5 seconds)
                            if time.time() - js_body_data['timestamp'] < 5:
                                req_body = js_body_data['body']
                                req_body_type = js_body_data['body_type']
                # Method 2: If still not captured, try to capture it NOW (same place where response body is captured)
                if (req_body is None or req_body == '') and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                    try:
                        # Try post_data first (check if it's bytes or str)
                        if request.post_data:
                            post_data = request.post_data
                            # Check if post_data is bytes or str
                            if isinstance(post_data, bytes):
                                try:
                                    body_str = post_data.decode('utf-8', errors='ignore')
                                    # Try to parse as JSON
                                    try:
                                        req_body = json.loads(body_str)
                                        req_body_type = 'json'
                                    except:
                                        req_body = body_str
                                        req_body_type = 'text'
                                except:
                                    # Binary data - encode as base64 for display
                                    req_body = base64.b64encode(post_data).decode('ascii')
                                    req_body_type = 'binary'
                            elif isinstance(post_data, str):
                                # Already a string
                                try:
                                    req_body = json.loads(post_data)
                                    req_body_type = 'json'
                                except:
                                    req_body = post_data
                                    req_body_type = 'text'
                            else:
                                req_body = str(post_data)
                                req_body_type = 'text'
                        # If post_data is not available, try post_data_buffer (property, not method)
                        elif hasattr(request, 'post_data_buffer') and request.post_data_buffer:
                            post_buffer = request.post_data_buffer
                            if isinstance(post_buffer, bytes):
                                try:
                                    body_str = post_buffer.decode('utf-8', errors='ignore')
                                    try:
                                        req_body = json.loads(body_str)
                                        req_body_type = 'json'
                                    except:
                                        req_body = body_str
                                        req_body_type = 'text'
                                except:
                                    req_body = base64.b64encode(post_buffer).decode('ascii')
                                    req_body_type = 'binary'
                            else:
                                req_body = str(post_buffer) if post_buffer else ''
                                req_body_type = 'text' if post_buffer else 'empty'
                        else:
                            req_body = ''
                            req_body_type = 'empty'
                    except Exception as e:
                        # If all methods fail, set empty
                        if CONFIG['debug']:
                            print(f"⚠️  Error getting request body: {e}")
                        req_body = ''
                        req_body_type = 'empty'
                
                # Update request_data with captured body
                # For GET requests, body should be None or empty
                # For POST/PUT/PATCH/DELETE, body should be captured
                if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
                    request_data['body'] = req_body if req_body is not None else ''
                    request_data['body_type'] = req_body_type if req_body_type is not None else 'empty'
                else:
                    # GET requests don't have body
                    request_data['body'] = None
                    request_data['body_type'] = None
                
                # Update the stored request data
                self._request_data_map[request_id] = request_data
                
                # Log ALL requests (GET, POST, PUT, PATCH, DELETE) - no filtering
                # Always log, regardless of method
                data = {
                    'timestamp': request_data.get('timestamp', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())),
                    'type': 'request_response',
                    'url': response.url,
                    'method': request_data.get('method', request.method),
                    'request': {
                        'headers': request_data.get('headers', dict(request.headers)),
                        'query_params': request_data.get('query_params', {}),
                        'body': request_data.get('body'),  # Use from request_data
                        'body_type': request_data.get('body_type')  # Use from request_data
                    },
                    'response': {
                        'status': response.status,
                        'status_text': response.status_text,
                        'headers': dict(response.headers),
                        'body': response_body,
                        'body_type': response_body_type
                    }
                }
                
                # Write log entry (check if activity_logger is initialized and enabled)
                if not activity_logger:
                    print(f"⚠️ [ERROR] activity_logger is None, cannot log request_response for {request.method} {response.url}")
                    return
                
                if not activity_logger.enabled:
                    print(f"⚠️ [ERROR] activity_logger is disabled, cannot log request_response for {request.method} {response.url}")
                    return
                
                try:
                    # Write to log file
                    activity_logger._write_log(data)
                except Exception as log_error:
                    print(f"⚠️ [ERROR] Error writing request_response log for {request.method} {response.url}: {log_error}")
                    import traceback
                    traceback.print_exc()
                
                # Clean up
                if request_id in self._request_data_map:
                    del self._request_data_map[request_id]
            
            # Setup response handler
            page.on('response', handle_response)
        
        # Log console messages
        if self.log_config['log_console']:
            async def handle_console(msg):
                if not activity_logger:
                    print(f"⚠️ [ERROR] activity_logger is None, cannot log console message: {msg.text}")
                    return
                if not activity_logger.enabled:
                    print(f"⚠️ [ERROR] activity_logger is disabled, cannot log console message: {msg.text}")
                    return
                try:
                    activity_logger.log_console(msg.text, level=msg.type)
                    # Always log first few console messages to verify it's working
                except Exception as e:
                    print(f"⚠️ [ERROR] Error logging console message: {e}")
                    import traceback
                    traceback.print_exc()
            
            page.on('console', handle_console)
        
        # Expose functions for JavaScript to call back to Python (must be before add_init_script)
        if self.log_config['log_localstorage'] or self.log_config['log_sessionstorage']:
            async def log_storage(storage_type, key, value, action):
                try:
                    if activity_logger:
                        activity_logger.log_storage(storage_type, key, value, action)
                except Exception as e:
                    if CONFIG['debug']:
                        print(f"⚠️  Error logging storage: {e}")
            
            page.expose_function('__mitm_log_storage', log_storage)
        
        # Expose function for interaction logging
        if self.log_config['log_interactions']:
            async def log_interaction(interaction_type, details):
                try:
                    if activity_logger:
                        activity_logger.log_interaction(interaction_type, details)
                except Exception as e:
                    if CONFIG['debug']:
                        print(f"⚠️  Error logging interaction: {e}")
            
            page.expose_function('__mitm_log_interaction', log_interaction)
        
        # Inject JavaScript to monitor storage (must be after expose_function)
        if self.log_config['log_localstorage'] or self.log_config['log_sessionstorage']:
            # Inject storage monitoring script
            storage_script = """
                (function() {
                    function setupStorageMonitoring() {
                        // Monitor localStorage
                        if (window.localStorage) {
                            try {
                                const originalSetItem = localStorage.setItem;
                                const originalRemoveItem = localStorage.removeItem;
                                const originalClear = localStorage.clear;
                                
                                localStorage.setItem = function(key, value) {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('localStorage', key, value, 'set').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalSetItem.apply(this, arguments);
                                };
                                
                                localStorage.removeItem = function(key) {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('localStorage', key, null, 'remove').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalRemoveItem.apply(this, arguments);
                                };
                                
                                localStorage.clear = function() {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('localStorage', null, null, 'clear').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalClear.apply(this, arguments);
                                };
                            } catch(e) {
                                console.error('Failed to setup localStorage monitoring:', e);
                            }
                        }
                        
                        // Monitor sessionStorage
                        if (window.sessionStorage) {
                            try {
                                const originalSetItem = sessionStorage.setItem;
                                const originalRemoveItem = sessionStorage.removeItem;
                                const originalClear = sessionStorage.clear;
                                
                                sessionStorage.setItem = function(key, value) {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('sessionStorage', key, value, 'set').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalSetItem.apply(this, arguments);
                                };
                                
                                sessionStorage.removeItem = function(key) {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('sessionStorage', key, null, 'remove').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalRemoveItem.apply(this, arguments);
                                };
                                
                                sessionStorage.clear = function() {
                                    try {
                                        if (window.__mitm_log_storage) {
                                            window.__mitm_log_storage('sessionStorage', null, null, 'clear').catch(() => {});
                                        }
                                    } catch(e) {
                                        console.error('Storage logging error:', e);
                                    }
                                    return originalClear.apply(this, arguments);
                                };
                            } catch(e) {
                                console.error('Failed to setup sessionStorage monitoring:', e);
                            }
                        }
                    }
                    
                    // Setup immediately if DOM is ready
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', setupStorageMonitoring);
                    } else {
                        setupStorageMonitoring();
                    }
                    
                    // Also setup on page load
                    window.addEventListener('load', setupStorageMonitoring);
                    
                    // Setup after a short delay to ensure __mitm_log_storage is available
                    setTimeout(setupStorageMonitoring, 100);
                })();
            """
            self.page.add_init_script(storage_script)
        
        # Inject JavaScript to monitor form interactions and element details
        if self.log_config['log_interactions']:
            self.page.add_init_script("""
                (function() {
                    function getElementInfo(element) {
                        if (!element) return null;
                        
                        return {
                            tag: element.tagName?.toLowerCase() || '',
                            id: element.id || '',
                            name: element.name || '',
                            type: element.type || '',
                            className: element.className || '',
                            placeholder: element.placeholder || '',
                            value: element.value || '',
                            formId: element.form?.id || '',
                            formAction: element.form?.action || '',
                            formMethod: element.form?.method || '',
                            selector: element.id ? '#' + element.id : 
                                      element.className ? '.' + element.className.split(' ')[0] : 
                                      element.tagName?.toLowerCase() || ''
                        };
                    }
                    
                    function setupInteractionMonitoring() {
                        // Track typing in input fields
                        let typingTimeouts = new Map();
                        let lastValues = new Map();
                        
                        function handleInputChange(event) {
                            const element = event.target;
                            const elementInfo = getElementInfo(element);
                            
                            // Clear previous timeout
                            if (typingTimeouts.has(element)) {
                                clearTimeout(typingTimeouts.get(element));
                            }
                            
                            // Set new timeout to detect complete string
                            const timeout = setTimeout(() => {
                                const currentValue = element.value || '';
                                const lastValue = lastValues.get(element) || '';
                                
                                // If value changed significantly (complete string typed)
                                if (currentValue !== lastValue && currentValue.length > 0) {
                                    if (window.__mitm_log_interaction) {
                                        window.__mitm_log_interaction('input_complete', {
                                            element: elementInfo,
                                            value: currentValue,
                                            valueLength: currentValue.length,
                                            isPassword: element.type === 'password',
                                            isEmail: element.type === 'email' || element.name?.toLowerCase().includes('email'),
                                            isUsername: element.name?.toLowerCase().includes('user') || element.id?.toLowerCase().includes('user')
                                        }).catch(() => {});
                                    }
                                    lastValues.set(element, currentValue);
                                }
                            }, 1000); // Wait 1 second after last keystroke
                            
                            typingTimeouts.set(element, timeout);
                        }
                        
                        // Monitor all input fields
                        document.addEventListener('input', handleInputChange, true);
                        document.addEventListener('change', handleInputChange, true);
                        
                        // Monitor form submissions
                        document.addEventListener('submit', function(event) {
                            const form = event.target;
                            if (!form || !window.__mitm_log_interaction) return;
                            
                            const formData = {};
                            const inputs = form.querySelectorAll('input, textarea, select');
                            
                            inputs.forEach(input => {
                                if (input.name && input.value) {
                                    formData[input.name] = input.type === 'password' ? 
                                        '[PASSWORD]' : 
                                        input.value.length > 100 ? 
                                        input.value.substring(0, 100) + '...' : 
                                        input.value;
                                }
                            });
                            
                            window.__mitm_log_interaction('form_submit', {
                                formId: form.id || '',
                                formAction: form.action || '',
                                formMethod: form.method || 'get',
                                formData: formData,
                                element: getElementInfo(form)
                            }).catch(() => {});
                        }, true);
                        
                        // Monitor button clicks (especially submit buttons)
                        document.addEventListener('click', function(event) {
                            const element = event.target;
                            if (!element || !window.__mitm_log_interaction) return;
                            
                            const elementInfo = getElementInfo(element);
                            
                            // Check if it's a submit button or form button
                            if (element.type === 'submit' || 
                                element.tagName?.toLowerCase() === 'button' ||
                                element.getAttribute('role') === 'button') {
                                
                                window.__mitm_log_interaction('button_click', {
                                    element: elementInfo,
                                    buttonText: element.textContent?.trim() || element.value || '',
                                    isSubmit: element.type === 'submit' || element.form !== null
                                }).catch(() => {});
                            }
                        }, true);
                        
                        // Monitor focus events to track which field user is interacting with
                        document.addEventListener('focus', function(event) {
                            const element = event.target;
                            if (element && (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA')) {
                                if (window.__mitm_log_interaction) {
                                    window.__mitm_log_interaction('field_focus', {
                                        element: getElementInfo(element)
                                    }).catch(() => {});
                                }
                            }
                        }, true);
                    }
                    
                    // Setup immediately if DOM is ready
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', setupInteractionMonitoring);
                    } else {
                        setupInteractionMonitoring();
                    }
                    
                    // Also setup on page load
                    window.addEventListener('load', setupInteractionMonitoring);
                })();
            """)
    
    async def get_cookies(self):
        """Get all cookies"""
        if not self.context:
            return []
        try:
            cookies = await self.context.cookies()
            return cookies if cookies else []
        except Exception as e:
            print(f"⚠️  Error getting cookies: {e}")
            return []
    
    async def get_local_storage(self):
        """Get localStorage items"""
        if not self.page:
            return {}
        try:
            return await self.page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                }
            """)
        except:
            return {}
    
    async def get_session_storage(self):
        """Get sessionStorage items"""
        if not self.page:
            return {}
        try:
            return await self.page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        items[key] = sessionStorage.getItem(key);
                    }
                    return items;
                }
            """)
        except:
            return {}
    
    async def close(self):
        """Close browser"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            print(f"⚠️  Close error: {e}")

