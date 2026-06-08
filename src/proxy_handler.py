"""
HTTP Proxy Request Handler for Firelynx

This module contains the HTTPProxyHandler class that processes HTTP requests
from lynx through the Firefox backend. It handles GET, POST, and CONNECT methods,
manages internal proxy commands, and provides form submission capabilities.

The handler supports:
- HTTP request routing and processing
- Internal proxy commands (/check-result/, /mfa-continue, /modal-action,
  /filter-change, /form-submit, /search)
- Form submission with background processing
- HTTPS support via CONNECT method
- URL caching for redirects and polling
- Error handling and logging
"""

import time
import html
import logging
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, urljoin
from src.content_processor import ContentProcessor
from src.utils.javascript_loader import load_js_file
from src.utils.search import build_search_url

logger = logging.getLogger(__name__)


def handle_broken_pipe(operation_name):
    """Decorator to handle BrokenPipeError consistently across proxy operations"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except BrokenPipeError:
                logger.warning(f"Client disconnected during {operation_name} (broken pipe)")
            except Exception as e:
                logger.error(f"Error in {operation_name}: {e}")
                raise
        return wrapper
    return decorator


class HTTPProxyHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, proxy_base_url=None, **kwargs):
        self.proxy_base_url = proxy_base_url
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests through Firefox"""
        url = self.path

        # Handle internal proxy commands first (must be from localhost to our port)
        if url.startswith(self.proxy_base_url + "/"):
            # Extract the path after localhost:port/
            internal_path = url[len(self.proxy_base_url):]  # Keep the leading /

            if internal_path.startswith('/check-result/'):
                # Extract result ID from URL like /check-result/b0a727e9?t=123456
                result_id_with_params = internal_path.split('/check-result/', 1)[1]
                # Remove query parameters (everything after ?)
                result_id = result_id_with_params.split('?')[0]
                self.handle_check_result(result_id)
                return
            elif internal_path.startswith('/mfa-continue'):
                # Handle MFA Continue button - user indicates they approved on phone
                self.handle_mfa_continue()
                return
            elif internal_path.startswith('/modal-action'):
                # Handle modal dialog actions converted from JavaScript
                self.handle_modal_action()
                return
            elif internal_path.startswith('/search'):
                # Handle search form submissions
                self.handle_search()
                return
            else:
                # Unknown internal command
                self.send_error(404, f"Unknown internal command: {internal_path}")
                return

        # Handle external URLs and relative paths
        if url.startswith(('http://', 'https://')):
            target_url = url
        elif url.startswith('/'):
            # Absolute path - need to reconstruct with host
            host = self.headers.get('Host', 'localhost')
            scheme = 'https' if self.server.default_https else 'http'
            target_url = f"{scheme}://{host}{url}"
        else:
            # Relative URL - prepend https
            target_url = f"https://{url}"

        try:
            # Check if we have cached result for this URL
            #
            # CACHE EXPLANATION: We cache results to preserve lynx's address bar URL.
            # Two types of cached content:
            #
            # 1. PRE-RENDERED HTML (is_html=True): From interactive operations like filter
            #    changes and modal actions. These process instantly and cache the final HTML.
            #
            # 2. PAGE DATA (is_html=False): From form submissions which use background
            #    processing. These cache raw page data that gets rendered when served.
            #
            if hasattr(self.server, 'firefox_proxy') and target_url in self.server.firefox_proxy.url_cache:
                logger.info(f"Serving cached result for: {target_url}")
                cached_result = self.server.firefox_proxy.url_cache[target_url]

                # Check cache type and handle appropriately
                if cached_result.get('is_html'):
                    # Pre-rendered HTML from instant operations (filters, modals)
                    html_content = cached_result['content']
                    operation = cached_result.get('operation', 'unknown operation')
                    logger.info(f"Serving pre-rendered HTML from {operation}")
                else:
                    # Raw page data from background operations (form submissions)
                    html_content = self.create_html_output(cached_result)

                # Remove from cache after serving (one-time use)
                del self.server.firefox_proxy.url_cache[target_url]
                logger.info(f"Served and removed cached result for: {target_url}")

                # Send cached response
                self.send_lynx_response(html_content)
                return

            # No cached result - get content through Firefox normally
            html_content = self.server.firefox_backend.fetch_page(target_url)

            # Send response
            self.send_lynx_response(html_content)

        except Exception as e:
            self.send_error(500, f"Proxy error: {str(e)}")

    def do_CONNECT(self):
        """Handle CONNECT method for HTTPS via ProxySSL

        Acknowledge the CONNECT and keep connection alive for the next request.
        """
        logger.debug(f"CONNECT request for {self.path}")

        # Send connection established response
        self.send_response(200, 'Connection established')
        self.end_headers()

        # Keep connection alive for the next request
        self.close_connection = False

        logger.debug("CONNECT: Connection established, kept alive for next request")

    def do_POST(self):
        """Handle POST requests properly for form submissions"""
        try:
            # Get the content length and read the POST data
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else b''

            # Parse the URL
            parsed_url = urlparse(self.path)

            # Handle internal proxy commands first (same security as GET)
            if self.path.startswith(self.proxy_base_url + "/"):
                # Extract the path after localhost:port/
                internal_path = self.path[len(self.proxy_base_url):]  # Keep the leading /

                if internal_path.startswith('/modal-action'):
                    # Handle modal dialog actions
                    self.handle_modal_action(post_data)
                    return
                elif internal_path.startswith('/filter-change'):
                    # Handle content filter changes
                    self.handle_filter_change_post(post_data)
                    return
                elif internal_path.startswith('/form-submit'):
                    # Handle regular form submissions
                    self.handle_form_submit(post_data)
                    return
                else:
                    # Unknown internal command
                    self.send_error(404, f"Unknown internal POST command: {internal_path}")
                    return

            # Handle our special proxy URLs
            if parsed_url.path.startswith('/proxy/'):
                # Extract real URL from proxy path
                real_path = parsed_url.path[7:]  # Remove '/proxy/'
                if parsed_url.query:
                    real_url = f"{real_path}?{parsed_url.query}"
                else:
                    real_url = real_path

                # If it doesn't start with http, it's a relative URL
                if not real_url.startswith(('http://', 'https://')):
                    if hasattr(self.server, 'last_base_url') and self.server.last_base_url:
                        real_url = urljoin(self.server.last_base_url, real_url)
                    else:
                        real_url = 'https://' + real_url
            else:
                # Direct URL request
                real_url = self.path.lstrip('/')
                if not real_url.startswith(('http://', 'https://')):
                    real_url = 'https://' + real_url

            logger.info(f"POST request to: {real_url}")

            # For form submissions, send immediate response to prevent lynx timeout
            if self.is_form_submission(real_url, post_data):
                logger.info("Detected form submission - sending immediate response to prevent timeout")
                self.send_immediate_form_response(real_url, post_data)
            else:
                # Submit the form using Firefox
                page_data = self.server.firefox_backend.submit_form(real_url, post_data, dict(self.headers))

                # Remember base URL for relative links
                self.server.last_base_url = page_data['url']

                # Send HTML response
                self.send_html_response(page_data)

        except Exception as e:
            logger.error(f"POST error: {e}")
            try:
                self.send_error(500, f"POST error: {str(e)}")
            except BrokenPipeError:
                logger.warning("Client disconnected while sending error response")

    def send_no_cache_headers(self):
        """Add no-cache headers to prevent lynx from caching"""
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')

    def send_lynx_response(self, html_content, status=200, status_message=None):
        """
        Centralized method for sending all responses to lynx with consistent headers

        This ensures all responses to lynx have:
        - Proper no-cache headers (prevents lynx caching issues with filter switching)
        - Consistent Content-Type and encoding
        - Proper Content-Length calculation
        - Standardized response format

        Args:
            html_content: HTML content to send (str or bytes)
            status: HTTP status code (default: 200)
            status_message: Optional custom status message
        """
        # Convert content to bytes if it's a string
        if isinstance(html_content, str):
            content_bytes = html_content.encode('utf-8')
        else:
            content_bytes = html_content

        # Send response with consistent headers
        self.send_response(status, status_message)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content_bytes)))
        self.send_no_cache_headers()  # Prevent lynx caching issues
        self.end_headers()
        self.wfile.write(content_bytes)

    @handle_broken_pipe("HTML response")
    def send_html_response(self, page_data):
        """Send HTML response formatted for lynx"""
        html_content = self.create_html_output(page_data)

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html_content.encode('utf-8'))))
        self.send_no_cache_headers()
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
        self.wfile.flush()

    def create_html_output(self, page_data):
        """Create HTML formatted for lynx display with native link handling"""
        processor = ContentProcessor(self.server.firefox_backend, show_search_form=self.server.show_search_form)
        return processor.generate_final_html(page_data)

    def is_form_submission(self, url, post_data):
        """Check if this is a login form submission that needs special handling"""
        if not post_data:
            return False

        # Check for common login form indicators
        post_data_str = post_data.decode('utf-8') if isinstance(post_data, bytes) else str(post_data)
        login_indicators = ['login', 'password', 'pass=', 'email=', 'username=']

        return any(indicator in post_data_str.lower() for indicator in login_indicators)

    @handle_broken_pipe("immediate form response")
    def send_immediate_form_response(self, url, post_data):
        """Send immediate redirect for form submissions to start automatic polling

        Why this approach is needed:
        - Firefox form submission can take 5-15+ seconds for complex sites (login, etc.)
        - Lynx has a timeout and will give up if no response comes quickly
        - We can't make lynx wait longer or show progress bars
        - Solution: Return immediate 302 redirect to start automatic polling
        - Lynx follows redirects automatically (up to 10 by default)
        - Each redirect waits up to 5 seconds for Firefox, then redirects again if needed
        - This creates seamless polling without user intervention
        """
        # Generate unique result ID
        result_id = str(uuid.uuid4())[:8]

        # Store initial processing status with timestamp
        self.server.firefox_proxy.form_results[result_id] = {
            'status': 'processing',
            'result': None,
            'start_time': time.time()
        }

        # Send immediate redirect to result checker
        redirect_url = f"{self.proxy_base_url}/check-result/{result_id}"

        self.send_response(302, 'Found')
        self.send_header('Location', redirect_url)
        self.end_headers()
        self.wfile.flush()

        logger.info(f"Sent immediate redirect to {redirect_url} for automatic polling")

        # Start background form processing
        def background_submit():
            try:
                logger.info(f"Starting background form submission for result ID: {result_id}")
                # Submit form using Firefox backend
                result = self.server.firefox_backend.submit_form(url, post_data, {})

                # Store the result - IMPORTANT: This replaces the entire dictionary!
                # Any code holding references to the old dictionary won't see this change.
                # That's why our polling code gets fresh references each time.
                self.server.firefox_proxy.form_results[result_id] = {
                    'status': 'completed',
                    'result': result
                }

                logger.info(f"Background form submission completed for ID: {result_id}")
                logger.info(f"Result title: {result.get('title', 'Unknown')}")
                logger.info(f"Result URL: {result.get('url', 'Unknown')}")
                # Log content sizes only, never page content itself: this path handles
                # login result pages and must not leak their contents into log files.
                logger.debug(f"Content length: {len(result.get('content', ''))} chars, "
                             f"HTML length: {len(result.get('htmlContent', ''))} chars")

                # Check for multi-factor authentication indicators
                is_mfa = self.server.firefox_backend.is_mfa_challenge_page(result)
                if is_mfa:
                    logger.info("🔐 DETECTED MFA challenge page - user needs to complete additional authentication")
                elif 'error' in result.get('title', '').lower():
                    logger.warning(f"Possible login error: {result.get('title', '')}")
                else:
                    logger.info("Standard page completion (no MFA detected)")
            except Exception as e:
                logger.error(f"Background form submission failed: {e}")
                # Store error result
                self.server.firefox_proxy.form_results[result_id] = {
                    'status': 'completed',
                    'result': {
                        'title': 'Error',
                        'content': f'Form submission failed: {str(e)}',
                        'url': url,
                        'links': []
                    }
                }

        # Start background thread
        thread = threading.Thread(target=background_submit)
        thread.daemon = True
        thread.start()
        logger.info("Background form processing started")

    def handle_check_result(self, result_id):
        """Handle check-result requests with automatic redirect polling

        Implements the polling mechanism:
        1. Wait up to 5 seconds checking for Firefox completion every 500ms
        2. If Firefox completes → return final result page
        3. If still processing → send 302 redirect with counter to continue polling
        4. Counter in URL (?c=123) prevents lynx from caching responses
        5. Lynx automatically follows redirects, creating seamless user experience
        6. Timeout after 30 seconds total to prevent infinite loops
        """
        try:
            # Check if result exists
            if result_id not in self.server.firefox_proxy.form_results:
                self.send_error(404, f"Result ID {result_id} not found")
                return

            result_data = self.server.firefox_proxy.form_results[result_id]

            # Check for timeout (30 seconds)
            current_time = time.time()
            if result_data['status'] == 'processing' and (current_time - result_data['start_time']) > 30:
                # Timeout - mark as failed
                result_data['status'] = 'completed'
                result_data['result'] = {
                    'title': 'Form Submission Timeout',
                    'content': 'Form submission took too long and timed out. This usually means the form failed to submit properly. Please try going back to the previous page and submitting again.',
                    'url': 'timeout',
                    'links': [{'text': 'Go back', 'url': 'javascript:history.back()'}]
                }

            if result_data['status'] == 'processing':
                # Still processing - wait up to 5 seconds for result or redirect to continue polling
                logger.info(f"Result {result_id} still processing, waiting up to 5 seconds...")

                # Wait in small increments, checking for completion
                max_wait_time = 5.0  # 5 seconds
                check_interval = 0.5  # Check every 500ms
                waited = 0.0

                while waited < max_wait_time:
                    time.sleep(check_interval)
                    waited += check_interval

                    # Check if processing completed - get fresh reference each time
                    current_result_data = self.server.firefox_proxy.form_results.get(result_id)
                    if current_result_data and current_result_data['status'] != 'processing':
                        logger.info(f"Result {result_id} completed while waiting (after {waited:.1f}s)")
                        # Update our local reference
                        result_data = current_result_data
                        break

                # Check status again after waiting
                if result_data['status'] == 'processing':
                    # Still not ready after 5 seconds - redirect to continue polling
                    self.server.firefox_proxy.url_counter += 1
                    counter = self.server.firefox_proxy.url_counter
                    redirect_url = f"{self.proxy_base_url}/check-result/{result_id}?c={counter}"

                    logger.info(f"Result {result_id} still processing after {max_wait_time}s, redirecting to {redirect_url}")

                    self.send_response(302, 'Found')
                    self.send_header('Location', redirect_url)
                    self.end_headers()
                    return

            # Processing completed (either was already done, or completed while waiting)
            result = result_data['result']

            # Cache the result and redirect to the actual URL so lynx shows correct current URL
            if 'url' in result and result['url']:
                actual_url = result['url']
                # Keep the original URL (HTTPS or HTTP) - lynx will handle HTTPS via CONNECT
                display_url = actual_url

                # Check if this result contains MFA state - if so, don't redirect
                is_mfa_page = self.server.firefox_backend.is_mfa_challenge_page(result)

                if is_mfa_page:
                    # MFA detected - serve content directly instead of redirecting
                    logger.info(f"🔐 MFA detected in result - serving content directly instead of redirecting")
                    html_content = self.create_html_output(result)

                    self.send_lynx_response(html_content)

                    # Clean up old result
                    del self.server.firefox_proxy.form_results[result_id]
                    logger.info(f"Result {result_id} served directly with MFA UI")
                    return

                # No MFA - proceed with normal redirect
                # Cache the result content so we can serve it when lynx requests the actual URL
                self.server.firefox_proxy.url_cache[display_url] = result
                logger.info(f"Cached result for URL: {display_url}")

                # Redirect to the actual URL instead of serving content directly
                logger.info(f"Redirecting to actual URL: {display_url}")
                self.send_response(302, 'Found')
                self.send_header('Location', display_url)
                self.end_headers()

                # Clean up old result
                del self.server.firefox_proxy.form_results[result_id]
                logger.info(f"Result {result_id} cached and redirect sent")
                return

            # Fallback: if no URL in result, serve directly (shouldn't normally happen)
            html_content = self.create_html_output(result)

            self.send_lynx_response(html_content)

            # Clean up old result
            del self.server.firefox_proxy.form_results[result_id]
            logger.info(f"Result {result_id} served directly (no URL for caching)")

        except Exception as e:
            logger.error(f"Error handling check-result: {e}")
            self.send_error(500, f"Error checking result: {str(e)}")

    def handle_mfa_continue(self):
        """Handle MFA Continue button - check if user's phone approval went through

        Critical fix for base domain issue: Instead of serving MFA content directly
        from the localhost/mfa-continue endpoint (which breaks relative links), this
        method now redirects lynx to the actual Facebook URL when MFA is successful.

        Process:
            1. Parse original URL from query parameters
            2. Check current Firefox state without navigation
            3. Re-run comprehensive MFA detection on current page
            4. If MFA complete: Cache result and redirect to actual Facebook URL
            5. If MFA pending: Show "still waiting" message with retry button

        Base Domain Fix:
            - OLD: Served content directly from localhost/mfa-continue (wrong domain)
            - NEW: 302 redirect to actual Facebook URL (correct domain)
            - Benefits: Relative links work, modal forms get correct port, proper URLs

        Security:
            - URL caching prevents repeated Firefox requests
            - No-cache headers prevent stale lynx content
            - Validates URL parameters before processing
        """
        try:
            # Parse query parameters to get the original URL
            parsed = urlparse(self.path)
            query_params = parse_qs(parsed.query)
            original_url = query_params.get('url', [''])[0]

            if not original_url:
                self.send_error(400, "Missing URL parameter")
                return

            logger.info(f"MFA Continue: Re-checking Facebook state (current page, not navigating)")

            # Get current Firefox state without navigating - check if approval went through
            page_data = self.server.firefox_backend.extract_content_from_current_page()

            # Re-run comprehensive MFA detection
            mfa_still_needed = self.server.firefox_backend.is_mfa_challenge_page(page_data)

            if not mfa_still_needed:
                # Success! MFA completed - redirect to actual Facebook page
                logger.info("🎉 MFA Continue: Login successful! Approval went through.")

                # Get the current Facebook URL that Firefox is actually on
                current_fb_url = page_data.get('url', original_url)

                # Cache the successful page result so it's available when lynx requests it
                if hasattr(self.server, 'firefox_proxy'):
                    self.server.firefox_proxy.url_cache[current_fb_url] = page_data
                    logger.info(f"Cached successful result for: {current_fb_url}")

                # CRITICAL BASE DOMAIN FIX: Redirect lynx to the actual Facebook URL
                # This fixes the issue where lynx would stay on localhost/mfa-continue
                # which breaks relative links and modal form submissions
                redirect_url = current_fb_url
                logger.info(f"Redirecting to actual Facebook page: {redirect_url}")

                self.send_response(302, 'Found')
                self.send_header('Location', redirect_url)
                self.send_no_cache_headers()
                self.end_headers()
            else:
                # Still waiting - show updated waiting message
                logger.info("⏳ MFA Continue: Still waiting for approval...")

                # Create updated message with "still waiting" notice
                waiting_notice = f'''<div style="border: 2px solid orange; padding: 10px; margin: 10px 0;">
<p><strong>⏳ STILL WAITING FOR APPROVAL</strong></p>
<p>Facebook hasn't detected your approval yet.</p>
<p><strong>Please check:</strong></p>
<ul>
<li>Did you <strong>actually tap "Approve"</strong> on your phone?</li>
<li>Is your phone connected to the internet?</li>
<li>Try waiting 10-15 more seconds, then click Continue again</li>
</ul>
<form method="get" action="{self.proxy_base_url}/mfa-continue" style="margin-top: 15px;">
<input type="hidden" name="url" value="{html.escape(original_url)}">
<input type="submit" value="🔄 Check Again" style="background: #f39c12; color: white; padding: 8px 16px; border: none; font-size: 14px;">
</form>
</div>
'''

                # Show the page with updated waiting message
                page_data_with_notice = page_data.copy()
                page_data_with_notice['content'] = waiting_notice + "\n\n" + page_data.get('content', '')

                html_content = self.create_html_output(page_data_with_notice)
                self.send_lynx_response(html_content)

        except Exception as e:
            logger.error(f"Error handling MFA continue: {e}")
            self.send_error(500, f"Error checking MFA status: {str(e)}")

    def handle_modal_action(self, post_data=None):
        """Handle modal dialog actions from converted JavaScript dialogs

        Processes form submissions from modal dialogs that were converted by
        extract_content_from_current_page() from JavaScript-driven UI elements
        into accessible HTML forms.

        Args:
            post_data: Optional POST data (bytes or str). If None, reads from request

        Process:
            1. Parses form data to extract action, modal_type, element_id
            2. Executes original JavaScript action by clicking stored element reference
            3. Waits for page update and returns refreshed content
            4. Handles failures gracefully with error messaging

        Supported Actions:
            - trust_device: Facebook device trust approval
            - not_now: Skip device trust
            - dialog_ok, dialog_cancel: Generic dialog actions
            - allow_notifications, block_notifications: Permission requests

        Security:
            - Only processes requests to localhost:{port}/modal-action
            - Uses stored element references to prevent injection
            - Validates action parameters before execution
        """
        try:
            # Parse form data
            if post_data is None:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b''

            if isinstance(post_data, bytes):
                post_data = post_data.decode('utf-8')

            form_data = parse_qs(post_data)

            # Modal buttons use a single inline form (same pattern as the filter
            # buttons): the clicked submit button's name encodes "action|element_id".
            action = ''
            element_id = ''
            for param_name, param_values in form_data.items():
                if '|' in param_name and param_values:
                    parts = param_name.split('|', 1)
                    if len(parts) == 2:
                        action, element_id = parts
                        break

            logger.info(f"Modal action: {action}, element_id: {element_id}")

            if not action:
                self.send_error(400, "Missing action parameter")
                return

            # Load modal functions and execute the click action
            modal_js = load_js_file('modal-detection.js')

            click_js = f"""
            {modal_js}

            // Execute the modal click using the external function
            return clickModalElement('{element_id}', '{action}');
            """

            # Execute the click action
            success = self.server.firefox_backend.driver.execute_script(click_js)

            if success:
                logger.info(f"✅ Modal action '{action}' executed successfully")

                # Wait for page to update after the action
                time.sleep(1.5)

                # Get updated page content
                page_data = self.server.firefox_backend.extract_content_from_current_page()

                # Log modal conversion results for debugging
                modals_converted = page_data.get('modalsConverted', 0)
                if modals_converted > 0:
                    logger.info(f"🔄 {modals_converted} additional modals converted in updated page")

                # Send the updated page back to lynx using redirect to preserve URL
                html_content = self.create_html_output(page_data)
                original_url = page_data.get('url', page_data.get('display_url', ''))

                self.cache_and_redirect(
                    html_content=html_content,
                    original_url=original_url,
                    operation_name=f"modal action: {action}"
                )

            else:
                logger.warning(f"❌ Modal action '{action}' failed - element not found or not clickable")
                # Still get current page data to show user what's available
                page_data = self.server.firefox_backend.extract_content_from_current_page()

                # Add error notice to the page content
                error_notice = f'''
                <div style="border: 2px solid red; padding: 10px; margin: 10px; background: #ffe6e6;">
                    <h3>⚠️ ACTION FAILED</h3>
                    <p>Could not execute modal action: <strong>{action}</strong></p>
                    <p>The dialog element may have changed or disappeared.</p>
                </div>
                '''

                # Prepend error to content
                original_content = page_data.get('content', '')
                page_data['content'] = error_notice + '\n\n' + original_content

                html_content = self.create_html_output(page_data)
                original_url = page_data.get('url', page_data.get('display_url', ''))

                self.cache_and_redirect(
                    html_content=html_content,
                    original_url=original_url,
                    operation_name=f"modal action failed: {action}"
                )

        except Exception as e:
            logger.error(f"Error handling modal action: {e}")
            self.send_error(500, f"Modal action failed: {str(e)}")

    def handle_filter_change_post(self, post_data):
        """Handle content filter changes from form button submissions"""
        try:
            # Parse the POST data to get the filter level
            import urllib.parse
            parsed_data = urllib.parse.parse_qs(post_data.decode('utf-8'))
            logger.debug(f"Filter change POST data: {parsed_data}")

            # With single form and multiple submit buttons, the clicked button's value becomes the parameter
            filter_level = parsed_data.get('filter', [''])[0]

            if not filter_level:
                self.send_error(400, f"Missing filter parameter. Received data: {parsed_data}")
                return

            # Strip brackets from filter level (e.g., "[minimal]" -> "minimal")
            filter_level = filter_level.strip('[]')

            # Validate filter level
            valid_filters = ['minimal', 'balanced', 'all']
            if filter_level not in valid_filters:
                self.send_error(400, f"Invalid filter level: {filter_level}")
                return

            logger.info(f"🔍 Changing content filter to: {filter_level}")

            # Update the backend's current filter state
            self.server.firefox_backend.set_content_filter(filter_level)

            # Get current page data from Firefox (already loaded)
            page_data = self.server.firefox_backend.extract_content_from_current_page()

            # Generate HTML with the requested filter level
            from src.content_processor import ContentProcessor
            processor = ContentProcessor(self.server.firefox_backend, show_search_form=self.server.show_search_form)
            filtered_content = processor.generate_final_html(
                page_data, filter_level=filter_level
            )

            # Get current URL and redirect back to preserve address bar
            original_url = page_data.get('url', self.server.firefox_backend.driver.current_url)
            self.cache_and_redirect(
                html_content=filtered_content,
                original_url=original_url,
                operation_name=f"filter change to {filter_level}"
            )

        except Exception as e:
            logger.error(f"Error changing filter level: {e}")
            self.send_error(500, f"Filter change failed: {str(e)}")


    def cache_and_redirect(self, html_content, original_url, operation_name):
        """
        Cache HTML content and redirect back to original URL to preserve lynx address bar.

        WHY THIS IS NEEDED:

        Lynx is a traditional web browser that shows the URL of the current request in its
        address bar. When users submit proxy-internal forms like:

        - /filter-change (content filter changes)
        - /modal-action (JavaScript dialog actions)

        Lynx's address bar shows "http://localhost:8394/filter-change" instead of the
        real page URL like "https://example.com". This breaks the user experience because:

        1. User loses track of what site they're actually on
        2. Relative links in the page break (they resolve against localhost:8394)
        3. Bookmarking saves the wrong URL
        4. Browser history becomes useless
        5. User can't refresh the page (would hit proxy URL, not real page)

        THE SOLUTION:

        Instead of returning content directly from proxy commands, we:

        1. Process the request (apply filter, execute modal action, etc.)
        2. Generate the resulting HTML content
        3. Cache the HTML using the REAL page URL as the key
        4. Send HTTP 302 redirect back to the real page URL
        5. When lynx follows the redirect, serve the cached HTML
        6. Lynx address bar shows the correct URL, everything works normally

        This is the same pattern used by form submissions, but those need background
        processing due to timeout concerns. Interactive operations like filters and
        modals can process instantly, so they use this simpler single-redirect approach.

        Args:
            html_content: Pre-rendered HTML to cache and serve after redirect
            original_url: Real page URL to redirect back to (preserves lynx address bar)
            operation_name: Description for logging (e.g., "minimal filter", "modal action")
        """
        try:
            if not original_url or original_url.startswith('http://localhost'):
                # Fallback - serve content directly if we can't redirect properly
                self.send_lynx_response(html_content)
                logger.info(f"✅ {operation_name} completed (direct response)")
                return

            # Store result in cache for redirect
            self.server.firefox_proxy.url_cache[original_url] = {
                'content': html_content,
                'is_html': True,  # Flag to indicate pre-rendered HTML
                'operation': operation_name
            }

            # Send redirect back to original URL
            self.send_response(302, 'Found')
            self.send_header('Location', original_url)
            self.send_no_cache_headers()
            self.end_headers()

            logger.info(f"✅ {operation_name} completed, redirecting to: {original_url}")

        except Exception as e:
            logger.error(f"Error in cache_and_redirect for {operation_name}: {e}")
            self.send_error(500, f"{operation_name} failed: {str(e)}")


    def handle_form_submit(self, post_data):
        """Handle regular form submissions to external sites via proxy"""
        try:
            # Extract target URL from the query parameters
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)

            target_url = query_params.get('target', [None])[0]
            if not target_url:
                self.send_error(400, "Missing target URL parameter")
                return

            target_url = unquote(target_url)

            logger.info(f"🔄 Form submission to: {target_url}")

            # Submit the form using Firefox
            page_data = self.server.firefox_backend.submit_form(target_url, post_data, dict(self.headers))

            # Send the response back to lynx
            html_content = self.create_html_output(page_data)

            self.send_lynx_response(html_content)

        except Exception as e:
            logger.error(f"Error handling form submission: {e}")
            self.send_error(500, f"Form submission failed: {str(e)}")

    def handle_search(self):
        """Handle search form submissions"""
        try:
            # Parse query parameters from the URL
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(self.path)
            query_params = parse_qs(parsed_url.query)

            # Extract search query and engine
            search_query = query_params.get('q', [''])[0].strip()
            search_engine = query_params.get('engine', ['duckduckgo'])[0].strip()

            if not search_query:
                self.send_error(400, "Missing search query parameter 'q'")
                return

            # Build target search URL based on engine
            target_url = build_search_url(search_query, search_engine)

            logger.info(f"🔍 Search: '{search_query}' via {search_engine.title()}")

            # Get search results through Firefox backend
            html_content = self.server.firefox_backend.fetch_page(target_url)

            # Cache result and redirect to preserve URL in lynx address bar
            self.cache_and_redirect(
                html_content=html_content,
                original_url=target_url,
                operation_name=f"search for '{search_query}' via {search_engine.title()}"
            )

        except Exception as e:
            logger.error(f"Error handling search: {e}")
            self.send_error(500, f"Search failed: {str(e)}")

    def log_message(self, format, *args):
        """Override to reduce log noise"""
        # Only log actual requests, not internal proxy chatter
        if args and len(args) > 1 and not args[1].startswith('200'):
            logger.debug(f"Proxy: {format % args}")
