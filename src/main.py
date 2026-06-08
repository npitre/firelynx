#!/usr/bin/env python3
"""
Firelynx Main Entry Point
Firefox HTTP Proxy for Lynx - Accessible Browser
"""

import sys
import time
import argparse
import logging
import os
import traceback

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import modular components
from src.proxy_server import FirefoxProxy, setup_signal_handlers


def main():
    """Main entry point for Firelynx"""
    parser = argparse.ArgumentParser(description='Firefox HTTP Proxy for Lynx - Accessible Browser')
    parser.add_argument('url', nargs='?', help='Starting URL for lynx')
    parser.add_argument('-p', '--port', type=int,
                        help='Proxy port (default: 8080 for --proxy-only, 8394 for normal mode)')
    parser.add_argument('--proxy-only', action='store_true',
                        help='Start proxy only, do not launch lynx')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging (to firelynx_debug.log unless --dump mode)')
    parser.add_argument('--verbose', action='store_true',
                        help='Enable verbose logging (INFO level, to firelynx_verbose.log unless --dump mode)')
    parser.add_argument('--logfile', type=str, metavar='PATH',
                        help='Specify log file path (default: stderr)')
    parser.add_argument('--dump', action='store_true',
                        help='Use lynx -dump for text output (no interactive mode)')
    parser.add_argument('-s', '--search', help='Search query (works with --dump)')
    parser.add_argument('--private', action='store_true',
                        help='Use temporary profile (no cookies/sessions persist, cleared after use)')
    parser.add_argument('--firefox-profile', type=str, metavar='PROFILE_NAME',
                        help='Use specific Firefox profile by name (default: uses persistent firelynx profile)')
    parser.add_argument('-e', '--engine', choices=['google', 'duckduckgo', 'bing'],
                        default='duckduckgo', help='Search engine (default: duckduckgo)')
    parser.add_argument('--content', choices=['minimal', 'balanced', 'all'],
                        default='balanced', help='Content filter level (default: balanced)')
    parser.add_argument('--search-form', action='store_true',
                        help='Show search form at the top of each page')

    args = parser.parse_args()

    # Set default port based on mode if not explicitly specified
    # Port choice distinction:
    # - 8080: Standard HTTP proxy port - signals "general purpose proxy server"
    # - 8394: Non-standard port - signals "internal communication, not public proxy"
    if args.port is None:
        if args.proxy_only:
            args.port = 8080  # Standard proxy port for --proxy-only mode
        else:
            args.port = 8394  # Private port for internal lynx-firelynx communication

    # Configure logging with automatic default log files:
    # 1. Default: ERROR level to stderr
    # 2. If --logfile specified: use that file
    # 3. If --debug: DEBUG level, and to firelynx_debug.log unless --dump mode or --logfile specified
    # 4. If --verbose: INFO level, and to firelynx_verbose.log unless --dump mode or --logfile specified

    # Determine log level (debug takes precedence over verbose)
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.ERROR

    # Determine log output destination
    if args.logfile:
        # --logfile specified: use that file
        log_handler = logging.FileHandler(args.logfile)
        log_destination = args.logfile
    elif log_level == logging.ERROR:
        # ERROR level: output to stderr
        log_handler = logging.StreamHandler(sys.stderr)
        log_destination = "stderr"
    elif args.dump:
        # --dump provided: output to stderr
        log_handler = logging.StreamHandler(sys.stderr)
        log_destination = "stderr"
    else:
        # Default: firelynx_debug.log or firelynx_verbose.log
        if args.debug:
            debug_log_file = os.path.join(os.getcwd(), 'firelynx_debug.log')
        else:  # verbose
            debug_log_file = os.path.join(os.getcwd(), 'firelynx_verbose.log')
        log_handler = logging.FileHandler(debug_log_file)
        log_destination = debug_log_file

    # Clear existing handlers and reconfigure
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Choose format based on output destination
    if log_destination == "stderr" and sys.stderr.isatty():
        # Terminal output: omit timestamp for cleaner display
        log_format = '%(levelname)s - %(message)s'
    else:
        # File output or non-terminal: include timestamp
        # Custom formatter to show time with milliseconds but no date
        class TimeOnlyFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                # Format time as HH:MM:SS,mmm (time only with milliseconds)
                ct = time.localtime(record.created)
                ms = int(record.msecs)
                return f"{ct.tm_hour:02d}:{ct.tm_min:02d}:{ct.tm_sec:02d},{ms:03d}"

        log_format = '%(asctime)s - %(levelname)s - %(message)s'
        log_handler.setFormatter(TimeOnlyFormatter(log_format))

    logging.basicConfig(
        level=log_level,
        handlers=[log_handler],
        force=True  # Override existing configuration
    )

    # Configure third-party library logging
    from src.utils.logging_config import set_selenium_log_level
    set_selenium_log_level(logging.ERROR if not args.debug else logging.WARNING)
    if not args.debug:
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)

    # Show logging info only if more verbose than default ERROR level
    if log_level < logging.ERROR:  # DEBUG=10, INFO=20, ERROR=40
        if log_level == logging.DEBUG:
            level_info = "DEBUG and above"
        elif log_level == logging.INFO:
            level_info = "INFO and above"
        print(f"Logging: {level_info} to {log_destination}")


    # Create proxy instance
    proxy = FirefoxProxy(
        port=args.port,
        use_private_profile=args.private,
        profile_name=args.firefox_profile,
        default_content_filter=args.content,
        show_search_form=args.search_form
    )

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers(proxy)

    # PROXY_BASE_URL is set by proxy.start() in proxy_server.py

    logger = logging.getLogger(__name__)

    try:
        proxy.start()
        time.sleep(1)  # Give proxy time to start

        if args.proxy_only:
            if args.dump and args.url:
                # Special diagnostic mode: --dump --proxy-only <url>
                # Fetch the URL through the proxy and dump the HTML that would go to lynx
                print("=== PROXY HTML OUTPUT (what lynx would receive) ===")
                try:
                    # Navigate to the URL and extract page data
                    proxy.firefox_backend.driver.get(args.url)
                    proxy.firefox_backend.hide_webdriver_traces()
                    proxy.firefox_backend.wait_for_interactive_elements_stable()

                    # Extract page data (dict format)
                    page_data = proxy.firefox_backend.extract_page_data()

                    # Generate the HTML that would be sent to lynx
                    from src.content_processor import ContentProcessor
                    processor = ContentProcessor(proxy.firefox_backend, show_search_form=proxy.show_search_form)
                    html_output = processor.create_lynx_html(page_data, args.url)
                    print(html_output)
                except Exception as e:
                    print(f"Error fetching page: {e}")
                    traceback.print_exc()
                return
            else:
                print(f"Proxy running. Set your browser to use HTTP proxy localhost:{proxy.port}")
                print("Press Ctrl+C to stop")
                while True:
                    time.sleep(1)
        elif args.dump:
            proxy.dump_url(args.url, args.search, args.engine)
        else:
            proxy.launch_lynx(args.url)

    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        if args.debug:
            traceback.print_exc()
    finally:
        proxy.stop()


if __name__ == '__main__':
    main()
