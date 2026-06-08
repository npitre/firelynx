"""
Firefox Proxy Server Module

This module contains the FirefoxProxy class that manages the HTTP proxy server
lifecycle, including server startup/shutdown, port management, result caching,
and integration with lynx for accessible browsing.

The FirefoxProxy provides:
- HTTP server setup and configuration
- Multi-instance support with automatic port selection
- Background form processing result caching
- Server lifecycle management (start/stop)
- Lynx integration with proxy environment setup
- Signal handling for graceful cleanup
- ProxySSL support for HTTPS navigation
"""

import os
import sys
import time
import logging
import threading
import signal
import socket
import subprocess
from http.server import HTTPServer

from .firefox_backend import FirefoxBackend
from .proxy_handler import HTTPProxyHandler
from .utils.search import build_search_url

logger = logging.getLogger(__name__)

# Global proxy server base URL - set at startup, used throughout the application
PROXY_BASE_URL = None


class FirefoxProxy:
    """HTTP Proxy server that routes requests through Firefox for accessible browsing"""

    def __init__(self, port=8394, default_https=True, use_private_profile=False, profile_name=None, default_content_filter='balanced', show_search_form=False):
        """Initialize the Firefox proxy server

        Args:
            port: Port number to bind the proxy server
            default_https: Whether to default to HTTPS URLs (default: True)
            use_private_profile: Use temporary Firefox profile that's cleaned up after use
            profile_name: Specific Firefox profile name to use (optional)
            default_content_filter: Content filtering level ('minimal', 'balanced', 'all')
            show_search_form: Whether to show search form at top of pages (default: False)
        """
        self.port = port
        self.default_https = default_https
        self.show_search_form = show_search_form
        self.firefox_backend = FirefoxBackend(
            use_private_profile=use_private_profile,
            profile_name=profile_name,
            default_content_filter=default_content_filter
        )
        self.server = None
        self.server_thread = None

        # Store background form submission results
        self.form_results = {}  # result_id -> {'status': 'processing'/'completed', 'result': data}

        # Counter for unique URLs to prevent caching
        self.url_counter = 0

        # Cache final results by URL so we can serve them when lynx requests the actual URL
        self.url_cache = {}  # url -> cached result data

    def find_available_port(self, start_port, max_attempts=10):
        """Find an available port starting from start_port

        Enables multiple browser instances to run simultaneously by automatically
        selecting the next available port when the requested port is in use.

        Args:
            start_port: Port number to start searching from
            max_attempts: Maximum number of consecutive ports to test (default: 10)

        Returns:
            int: First available port number found

        Raises:
            OSError: If no available port found in the range

        Usage:
            - Instance 1: Requests 8080, gets 8080
            - Instance 2: Requests 8080, gets 8081 (auto-selected)
            - Instance 3: Requests 8085, gets 8085 (if available)
        """
        for port_offset in range(max_attempts):
            test_port = start_port + port_offset
            try:
                # Test if port is available
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('localhost', test_port))
                logger.info(f"Found available port: {test_port}")
                return test_port
            except OSError:
                if port_offset == 0:
                    logger.info(f"Port {test_port} is already in use, trying next available port...")
                continue

        raise OSError(f"Could not find available port in range {start_port}-{start_port + max_attempts - 1}")

    def start(self):
        """Start the proxy server on available port"""
        # Find available port starting from requested port
        available_port = self.find_available_port(self.port)

        # Update port if we found a different one
        if available_port != self.port:
            logger.info(f"Using port {available_port} instead of requested {self.port}")
            self.port = available_port

        # Set proxy base URL first
        proxy_base_url = f"http://localhost:{self.port}"

        # Set global proxy base URL for backward compatibility
        global PROXY_BASE_URL
        PROXY_BASE_URL = proxy_base_url

        # Create handler factory that passes proxy_base_url to each handler instance
        def handler_factory(*args, **kwargs):
            return HTTPProxyHandler(*args, proxy_base_url=proxy_base_url, **kwargs)

        self.server = HTTPServer(('localhost', self.port), handler_factory)
        self.server.firefox_backend = self.firefox_backend
        self.server.default_https = self.default_https
        self.server.show_search_form = self.show_search_form
        self.server.firefox_proxy = self  # Add reference to the proxy instance

        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

        print(f"🚀 Firefox HTTP proxy started on localhost:{self.port}")
        logger.info(f"🚀 Firefox HTTP proxy started on localhost:{self.port}")
        return f"http://localhost:{self.port}"

    def prepare_lynx_env(self):
        """Prepare environment for lynx with proxy and ProxySSL support"""
        env = os.environ.copy()
        env['http_proxy'] = PROXY_BASE_URL
        env['HTTP_PROXY'] = PROXY_BASE_URL
        env['https_proxy'] = PROXY_BASE_URL
        env['HTTPS_PROXY'] = PROXY_BASE_URL

        # Add ProxySSL for HTTPS navigation
        proxyssl_lib = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'proxyssl', 'libproxyssl.so')
        if os.path.exists(proxyssl_lib):
            env['LD_PRELOAD'] = proxyssl_lib
            # Enable ProxySSL debug when our logger is at DEBUG level
            env['PROXYSSL_DEBUG'] = '1' if logger.isEnabledFor(logging.DEBUG) else '0'
            # Configure ProxySSL to intercept connections to our proxy port
            env['PROXYSSL_PORT'] = str(self.port)
            logger.info("ProxySSL enabled for HTTPS navigation")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"ProxySSL debug enabled, library: {proxyssl_lib}")
                logger.debug(f"ProxySSL configured for port: {self.port}")
        else:
            logger.warning(f"ProxySSL library not found at {proxyssl_lib}")
            logger.warning("HTTPS navigation may not work properly. Build with: cd proxyssl && make")

        return env

    def launch_lynx(self, start_url=None):
        """Launch lynx with proxy configuration"""
        env = self.prepare_lynx_env()

        print(f"Starting lynx with proxy {PROXY_BASE_URL}")
        if start_url:
            print(f"Initial URL: {start_url}")
            print("In lynx: Press 'g' to go to any URL (http:// or https://), 'q' to quit")
            print("All HTTPS links will work through the proxy!")
            print("Note: Using persistent Firefox profile - cookies/sessions will persist!")
            lynx_cmd = ['lynx', start_url]
        else:
            print("In lynx: Press 'g' to go to any URL (http:// or https://), 'q' to quit")
            print("All HTTPS links will work through the proxy!")
            print("Note: Using persistent Firefox profile - cookies/sessions will persist!")
            lynx_cmd = ['lynx']

        try:
            # Launch lynx with stderr captured to redirect ProxySSL debug output
            process = subprocess.Popen(lynx_cmd, env=env, stderr=subprocess.PIPE, text=True)

            # Read stderr in background thread to capture ProxySSL debug output
            import threading
            def stderr_reader():
                try:
                    for line in process.stderr:
                        line = line.rstrip()
                        if line:
                            logger.debug(line)
                except Exception:
                    pass

            stderr_thread = threading.Thread(target=stderr_reader, daemon=True)
            stderr_thread.start()

            # Wait for lynx to complete
            process.wait()
        except KeyboardInterrupt:
            # User pressed Ctrl+C in lynx - this is normal exit, no cleanup needed here
            # Cleanup will happen in the finally block of main()
            pass

    def dump_url(self, url=None, search_query=None, search_engine='duckduckgo'):
        """Use lynx -dump for text-only output"""
        env = self.prepare_lynx_env()

        # Determine target URL
        if search_query:
            target_url = build_search_url(search_query, search_engine)
            print(f"Searching {search_engine.title()} for: {search_query}")
        elif url:
            target_url = url
            if not target_url.startswith(('http://', 'https://')):
                target_url = 'https://' + target_url
            print(f"Loading: {target_url}")
        else:
            target_url = "https://duckduckgo.com"
            print("Loading: DuckDuckGo search page")

        lynx_url = target_url

        # Run lynx in dump mode
        try:
            result = subprocess.run(['lynx', '-dump', lynx_url],
                                  env=env, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                print("=" * 80)
                print(f"URL: {target_url}")
                print("=" * 80)
                print()
                print(result.stdout)
                print()
                print("=" * 80)
            else:
                print(f"Error: lynx failed with return code {result.returncode}")
                if result.stderr:
                    print(f"Error details: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("Error: lynx timed out after 30 seconds")
        except FileNotFoundError:
            print("Error: lynx not found. Please install lynx.")
        except Exception as e:
            print(f"Error running lynx: {e}")

    def stop(self):
        """Stop the proxy server and Firefox backend"""
        # Make this method idempotent to handle multiple calls gracefully
        if hasattr(self, '_stopped') and self._stopped:
            return

        self._stopped = True
        logger.info("Stopping proxy server...")
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            logger.info("Proxy server stopped")
        if self.firefox_backend:
            logger.info("Closing Firefox backend...")
            self.firefox_backend.close()
            logger.info("Firefox backend closed")


def setup_signal_handlers(proxy):
    """Set up signal handlers for graceful shutdown"""
    def signal_handler(sig, frame):
        print(f"\nReceived signal {sig}, stopping proxy...")
        try:
            proxy.stop()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        sys.exit(0)

    # Handle multiple signals that could terminate the process
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # pkill, systemctl stop, etc.
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)   # Terminal disconnect