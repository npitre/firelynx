#!/usr/bin/env python3
"""
Firefox Backend Module
Handles Firefox WebDriver management, content extraction, and form processing
"""

import sys
import time
import random
import re
import html
import logging
import os
import shutil
from urllib.parse import urljoin, urlparse, quote
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException
from .form_processor import FormProcessor
from .utils.javascript_loader import load_js_file

logger = logging.getLogger(__name__)

class FirefoxBackend:
    def __init__(self, wait_time=2, use_private_profile=False, profile_name=None, default_content_filter='balanced'):
        self.driver = None
        self.wait_time = wait_time
        self.use_private_profile = use_private_profile
        self.profile_name = profile_name
        self.temp_profile_path = None  # Track temp profile for cleanup
        self.current_content_filter = default_content_filter  # Active filter state (starts with CLI arg)
        self.setup_firefox()

        # Register cleanup handler for private mode
        if self.use_private_profile:
            import atexit
            atexit.register(self._cleanup_temp_profile)

    def _clear_profile_pref(self, profile_path, pref_name):
        """Remove a preference from the profile's prefs.js and user.js before Firefox starts.

        Selenium options.set_preference() writes to both prefs.js and user.js, and MERGES
        with existing content rather than replacing it. When we stop setting a preference
        (like general.useragent.override), the old value stays until explicitly removed.
        user.js takes priority over prefs.js, so both files must be cleared.
        """
        for filename in ('prefs.js', 'user.js'):
            pref_file = os.path.join(profile_path, filename)
            if not os.path.exists(pref_file):
                continue
            try:
                with open(pref_file, 'r') as f:
                    lines = f.readlines()
                new_lines = [l for l in lines if f'"{pref_name}"' not in l]
                if len(new_lines) != len(lines):
                    with open(pref_file, 'w') as f:
                        f.writelines(new_lines)
                    logger.info(f"Cleared {pref_name} from {filename}")
            except Exception as e:
                logger.debug(f"Could not clear {pref_name} from {filename}: {e}")

    def _remove_stale_profile_lock(self, profile_path):
        """Remove the Firefox profile lock if the locking process no longer exists.

        geckodriver kills Firefox without a graceful shutdown, so the lock file
        is not cleaned up on exit. This causes the next startup to wait ~90s
        for Firefox to time out waiting for the lock to be released.
        """
        lock_file = os.path.join(profile_path, 'lock')
        if not os.path.islink(lock_file):
            return
        try:
            # Lock symlink target format: "hostname:+PID"
            link_target = os.readlink(lock_file)
            _, pid_str = link_target.rsplit(':+', 1)
            pid = int(pid_str)
            try:
                os.kill(pid, 0)  # signal 0 just checks if process exists
                # Process is alive - another firelynx instance is running
                logger.error(f"Firefox profile is locked by running process (PID {pid})")
                logger.error("Another firelynx instance is already using this profile.")
                logger.error("Solutions:")
                logger.error("  1. Close the other firelynx instance first")
                logger.error("  2. Use --private flag to run a separate session")
                sys.exit(1)
            except ProcessLookupError:
                os.remove(lock_file)
                logger.info(f"Removed stale Firefox profile lock (PID {pid} is gone)")
        except Exception as e:
            logger.debug(f"Could not check profile lock: {e}")

    def setup_firefox(self):
        """Initialize Firefox with stealth settings"""
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        # Set up Firefox profile (persistent by default, temporary if --private)
        if not self.use_private_profile:
            try:
                profiles_dir = os.path.expanduser("~/.mozilla/firefox")

                if self.profile_name:
                    # Use specific named profile
                    profile_path = os.path.join(profiles_dir, self.profile_name)
                    self._remove_stale_profile_lock(profile_path)
                    self._clear_profile_pref(profile_path, 'general.useragent.override')
                    self._clear_profile_pref(profile_path, 'general.useragent.updates.enabled')
                    options.add_argument('-profile')
                    options.add_argument(profile_path)
                    logger.info(f"Using Firefox profile: {profile_path}")
                else:
                    # Use persistent firelynx profile
                    firelynx_profile_path = os.path.join(profiles_dir, 'firelynx-profile')
                    self._remove_stale_profile_lock(firelynx_profile_path)
                    self._clear_profile_pref(firelynx_profile_path, 'general.useragent.override')
                    self._clear_profile_pref(firelynx_profile_path, 'general.useragent.updates.enabled')
                    options.add_argument('-profile')
                    options.add_argument(firelynx_profile_path)
                    logger.info(f"Using persistent firelynx profile: {firelynx_profile_path}")

            except Exception as e:
                logger.warning(f"Failed to set up persistent profile, falling back to temporary: {e}")
                # Fall back to temporary profile (default Selenium behavior)
        else:
            logger.info("Using temporary profile (--private mode)")
        options.add_argument('--disable-gpu')

        # Performance optimizations - disable resource-heavy features
        options.add_argument('--disable-images')  # Major speed improvement

        # Disable legacy/security-risk plugins, but keep useful core functionality
        options.set_preference("plugin.state.flash", 0)  # Disable Flash specifically
        options.set_preference("media.autoplay.default", 5)  # Block autoplay media
        options.set_preference("dom.push.enabled", False)  # Disable push notifications

        # Stealth settings
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        options.set_preference("intl.accept_languages", "en-US, en;q=0.9")

        try:
            # Reuse a geckodriver that selenium-manager already cached (faster
            # startup), whatever version/platform it landed under. Otherwise let
            # Selenium download one on demand into ~/.cache/selenium/.
            import glob
            from selenium.webdriver.firefox.service import Service
            cached = sorted(glob.glob(os.path.expanduser(
                "~/.cache/selenium/geckodriver/*/*/geckodriver")))
            if cached:
                service = Service(cached[-1])
                self.driver = webdriver.Firefox(service=service, options=options)
            else:
                self.driver = webdriver.Firefox(options=options)
            self.driver.implicitly_wait(self.wait_time)
            self.driver.set_page_load_timeout(15)  # More generous timeout for slow sites
            self.driver.set_window_size(1366, 768)  # Realistic dimensions (headless reports 0x0)

            # Capture temp profile path for cleanup in private mode
            if self.use_private_profile:
                try:
                    self.temp_profile_path = self.driver.capabilities.get('moz:profile')
                    if self.temp_profile_path:
                        logger.debug(f"Private mode: Using temporary profile {self.temp_profile_path}")
                except Exception as e:
                    logger.debug(f"Could not read temporary profile path: {e}")

            self.install_stealth_extension()
            self.hide_webdriver_traces()
        except WebDriverException as e:
            error_msg = str(e)
            if "profile" in error_msg.lower() and ("lock" in error_msg.lower() or "use" in error_msg.lower()):
                logger.error("Firefox profile is locked (Firefox may already be running)")
                logger.error("Solutions:")
                logger.error("  1. Close existing Firefox instances")
                logger.error("  2. Use --private flag for temporary profile")
                logger.error("  3. Use --firefox-profile to specify a different profile")
                sys.exit(1)
            else:
                logger.error(f"Failed to start Firefox: {e}")
                sys.exit(1)

    def wait_for_interactive_elements_stable(self, max_wait=3, check_interval=0.5):
        """
        Wait for interactive elements to stabilize on the page
        Probes for button/form stability rather than guessing with fixed delays
        """
        try:
            check_js = """
            // Count interactive elements that could become buttons
            const buttons = document.querySelectorAll('button, input[type="submit"], [role="button"], a[role="button"]');
            const forms = document.querySelectorAll('form');
            const clickableElements = document.querySelectorAll('[onclick], [data-testid*="button"]');

            return {
                buttons: buttons.length,
                forms: forms.length,
                clickable: clickableElements.length,
                total: buttons.length + forms.length + clickableElements.length
            };
            """

            previous_count = None
            stable_checks = 0
            start_time = time.time()

            while time.time() - start_time < max_wait:
                try:
                    current_counts = self.driver.execute_script(check_js)
                    current_total = current_counts['total']

                    if previous_count is not None and current_total == previous_count:
                        stable_checks += 1
                        if stable_checks >= 2:  # Stable for 2 consecutive checks
                            logger.debug(f"🔍 Interactive elements stable: {current_counts}")
                            return
                    else:
                        stable_checks = 0

                    previous_count = current_total
                    time.sleep(check_interval)

                except Exception as e:
                    logger.debug(f"Element stability check failed: {e}")
                    break

            logger.debug("Interactive elements stability check timed out")

        except Exception as e:
            logger.debug(f"Interactive elements stability check error: {e}")

    def install_stealth_extension(self):
        """Install the stealth content script extension to hide WebDriver markers.

        The extension's content script runs at document_start (before any page
        JavaScript) using Firefox's wrappedJSObject API to hide navigator.webdriver.
        This is more effective than execute_script which runs after page load.
        """
        try:
            script_dir = os.path.dirname(os.path.dirname(__file__))
            ext_path = os.path.join(script_dir, 'extensions', 'stealth')
            if os.path.isdir(ext_path):
                self.driver.install_addon(ext_path, temporary=True)
                logger.debug("Stealth extension installed (document_start webdriver bypass)")
            else:
                logger.warning(f"Stealth extension not found at: {ext_path}")
        except Exception as e:
            logger.debug(f"Stealth extension install failed, using execute_script fallback: {e}")

    def hide_webdriver_traces(self):
        """Hide WebDriver detection markers via execute_script (post-page-load fallback).

        The stealth extension handles pre-page-load injection via document_start.
        This method runs after page load and covers additional fingerprinting signals
        or pages where the extension's injection was blocked by CSP.
        """
        stealth_js = """
        // Hide webdriver flag (may already be done by stealth extension)
        try {
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true
            });
        } catch (e) {}

        // Ensure realistic language settings
        try {
            if (!navigator.languages || navigator.languages.length === 0) {
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                    configurable: true
                });
            }
        } catch (e) {}

        // Fix window outer dimensions (headless Firefox reports 0)
        try {
            if (window.outerWidth === 0) {
                Object.defineProperty(window, 'outerWidth', {get: () => 1366, configurable: true});
                Object.defineProperty(window, 'outerHeight', {get: () => 768, configurable: true});
            }
        } catch (e) {}
        """
        try:
            self.driver.execute_script(stealth_js)
        except Exception as e:
            logger.debug(f"Stealth trace hiding via execute_script failed: {e}")

    def fetch_page(self, url):
        """Fetch page using Firefox and return HTML suitable for lynx"""
        try:
            time.sleep(random.uniform(0.3, 0.8))
            logger.debug(f"Firefox loading: {url}")

            self.driver.get(url)
            self.hide_webdriver_traces()

            try:
                WebDriverWait(self.driver, 3).until(
                    lambda driver: driver.execute_script("return document.readyState") == "complete"
                )
                # Probe for stable interactive content
                self.wait_for_interactive_elements_stable()
            except TimeoutException:
                pass

            # Extract content and enrich with SSL info and modal conversion
            page_data = self.extract_page_data()

            # Log which extraction method was used for optimization insights
            extraction_info = page_data.get('extraction', {})
            method = extraction_info.get('method', 'unknown')
            confidence = extraction_info.get('confidence', 0)
            source = extraction_info.get('source', 'unknown')
            content_length = extraction_info.get('contentLength', 0)

            logger.debug(f"📄 Content extracted via: {method} (confidence: {confidence:.2f}, source: {source})")
            logger.debug(f"📊 Content length: {content_length} chars, Links: {extraction_info.get('linkCount', 0)}")

            # Show all extraction methods that were tried for debugging
            all_methods = extraction_info.get('allMethods', [])
            if all_methods:
                method_summary = ', '.join([f"{m['method']}({m['confidence']:.2f})" for m in all_methods])
                logger.debug(f"🔍 All methods tried: {method_summary}")

            if extraction_info.get('hasStructuredData'):
                logger.debug("📋 Enhanced with structured data (JSON-LD/microdata)")

            # Special logging for combined extraction to understand what sections were found
            if method == 'combined_extraction':
                sections = page_data.get('sections', {})
                if sections:
                    logger.debug(f"🔗 Combined extraction - Interactive: {sections.get('interactive', {})}, Main: {sections.get('mainContent', {})}")

            # Import content processor for HTML generation
            from src.content_processor import ContentProcessor
            processor = ContentProcessor(self)
            html_content = processor.create_lynx_html(page_data, url)

            return html_content.encode('utf-8')

        except Exception as e:
            error_type = type(e).__name__
            logger.warning(f"Firefox page load failed for {url}: {error_type} - {e}")

            # Provide specific advice for common issues
            advice = ""
            if "timeout" in str(e).lower():
                advice = "<p><strong>Timeout Issue:</strong> The site is taking too long to load. This might be due to:</p><ul><li>Slow network connection</li><li>Site is overloaded or down</li><li>Site is blocking automated browsers</li></ul><p>Try waiting a moment and refreshing, or try a different site.</p>"
            elif "network" in str(e).lower() or "connection" in str(e).lower():
                advice = "<p><strong>Network Issue:</strong> Check your internet connection and try again.</p>"

            error_html = f"""<!DOCTYPE html>
<html>
<head><title>Firefox Proxy Error</title></head>
<body>
<h1>Firefox Proxy Error</h1>
<p>Failed to load <strong>{html.escape(url)}</strong></p>
<p><strong>Error:</strong> {html.escape(str(e))}</p>
{advice}
<p><a href="javascript:history.back()">← Go Back</a> | <a href="{html.escape(url)}">↻ Retry</a></p>
</body>
</html>"""
            return error_html.encode('utf-8')

    def get_ssl_info(self):
        """Extract SSL/TLS certificate information from Firefox"""
        try:
            # Get SSL/TLS information using JavaScript
            ssl_js = """
            // Try to get SSL/TLS info from various browser APIs
            const sslInfo = {
                secure: location.protocol === 'https:',
                protocol: location.protocol,
                hostname: location.hostname,
            };

            // Check if page is served over HTTPS
            if (location.protocol === 'https:') {
                // Try to get certificate info from browser security APIs
                // Note: Most detailed cert info requires special permissions
                sslInfo.is_secure = true;
                sslInfo.mixed_content = false;

                // Check for mixed content warnings
                const mixedContentElements = document.querySelectorAll('[src^="http://"], [href^="http://"]');
                if (mixedContentElements.length > 0 && location.protocol === 'https:') {
                    sslInfo.mixed_content = true;
                    sslInfo.mixed_content_count = mixedContentElements.length;
                }

                // Basic certificate validation check (limited info available)
                sslInfo.certificate_valid = true; // If we got here, basic validation passed
            } else {
                sslInfo.is_secure = false;
            }

            return sslInfo;
            """

            ssl_info = self.driver.execute_script(ssl_js)
            return ssl_info

        except Exception as e:
            logger.warning(f"Failed to get SSL info: {e}")
            return {
                'secure': False,
                'error': f'SSL info unavailable: {str(e)}'
            }

    def extract_page_data(self):
        """
        Extract page data using hybrid content extraction strategy.

        This method implements a multi-layered approach to content extraction:
        1. Mozilla Readability.js - proven algorithm from Firefox Reader Mode
        2. Accessibility-aware semantic analysis using ARIA roles and HTML5 landmarks
        3. Enhanced scoring system to avoid brittle CSS selector dependencies
        4. Structured data extraction for additional context

        Design rationale: Previous hardcoded selector approach (['main', 'article'])
        was fragile and failed on many sites. This hybrid approach provides better
        accuracy across diverse web content while maintaining real-time performance.
        """
        try:
            # First, try Python-side DOM processing for form pages
            raw_dom = self.extract_raw_dom()
            if raw_dom:
                python_result = self.process_dom_python_side(raw_dom)
                if python_result:
                    logger.info("✅ Using Python-side DOM extraction for form page")
                    return python_result

            # Fall back to JavaScript extraction for non-form pages
            # Use shared extraction method for consistency
            return self.extract_content_from_current_page()

        except Exception as e:
            return {
                'title': 'Error',
                'content': f'Content extraction failed: {str(e)}',
                'links': [],
                'url': 'unknown'
            }

    def enrich_page_data(self, page_data):
        """Add metadata and process modals for page data

        This method enriches page data with:
        - URL normalization (actual_url, display_url)
        - SSL/TLS security information
        - Modal element detection and conversion to forms

        Used by both initial page loads and filter changes to ensure consistency.

        Args:
            page_data: Dictionary containing extracted page content

        Returns:
            dict: Enriched page data with complete metadata
        """
        if not page_data:
            return page_data

        # Normalize URL fields for consistent display
        url = page_data.get('url', '')
        if url and not page_data.get('actual_url'):
            page_data['actual_url'] = url
        if url and not page_data.get('display_url'):
            page_data['display_url'] = page_data.get('actual_url', url)

        # Add SSL/TLS info to show security status
        ssl_info = self.get_ssl_info()
        page_data['ssl_info'] = ssl_info

        # Check for modal elements and convert them
        modal_elements = page_data.get('modalElements', {})
        total_modal_elements = modal_elements.get('totalElements', 0)

        if total_modal_elements > 0:
            logger.info(f"🔧 Modal conversion: {total_modal_elements} elements detected")
            form_processor = FormProcessor(self)
            page_data = form_processor.convert_modal_elements_to_forms(page_data)
            logger.info("🔧 Modal conversion completed")

        return page_data

    def is_mfa_challenge_page(self, page_data):
        """Detect whether the current page is an MFA challenge.

        Thin delegate to FormProcessor (which owns the DOM-based detection) so
        callers holding a FirefoxBackend reference can run MFA detection directly.
        """
        return FormProcessor(self).is_mfa_challenge_page(page_data)

    def extract_content_from_current_page(self):
        """Shared content extraction logic for both new page loads and filter changes

        This method contains the core JavaScript-based content extraction that should
        produce consistent results whether we're loading a new page or switching filters.

        Returns:
            dict: Page data with extracted content, links, and metadata
        """
        try:
            # Load external JavaScript modules from js/
            readability_source = load_js_file('readability.js')
            content_extraction_js = load_js_file('content-extraction.js')
            modal_detection_js = load_js_file('modal-detection.js')
            fallback_extraction_js = load_js_file('fallback-extraction.js')

            # First try: Advanced extraction with modal detection
            extraction_js = f"""
            // === PHASE 1: INJECT READABILITY.JS ===
            // Mozilla's battle-tested content extraction algorithm
            // Used in Firefox Reader Mode - handles link density, semantic scoring
            {readability_source}

            // === PHASE 2: LOAD MODAL DETECTION ===
            {modal_detection_js}

            // === PHASE 3: LOAD CONTENT EXTRACTION FUNCTIONS ===
            {content_extraction_js}

            // === PHASE 4: EXECUTE EXTRACTION ===
            return executeContentExtraction();
            """

            result = self.driver.execute_script(extraction_js)

            # Debug modal elements detection errors
            modal_elements = result.get('modalElements', {}) if result else {}
            modal_error = modal_elements.get('error')

            if modal_error:
                logger.error(f"🔧 Modal detection JavaScript error: {modal_error}")
                if modal_elements.get('stack'):
                    logger.error(f"🔧 JavaScript stack trace: {modal_elements.get('stack')}")

            # Check if fallback extraction is needed
            if result and result.get('needsFallback'):
                logger.info(f"🔄 Fallback extraction needed: {result.get('fallbackReason', 'Unknown reason')}")

                # Execute fallback extraction with modal detection
                fallback_js = f"""
                // === LOAD MODAL DETECTION ===
                {modal_detection_js}

                // === LOAD FALLBACK EXTRACTION ===
                {fallback_extraction_js}

                // === EXECUTE FALLBACK EXTRACTION WITH MODAL DETECTION ===
                const result = executeFallbackExtraction();
                result.modalElements = detectModalElements();
                return result;
                """

                fallback_result = self.driver.execute_script(fallback_js)
                logger.info("✅ Fallback extraction completed")

                # Enrich fallback result with SSL info and modal conversion
                return self.enrich_page_data(fallback_result)

            # Enrich result with SSL info and modal conversion
            return self.enrich_page_data(result)

        except Exception as e:
            logger.error(f"Content extraction failed: {e}")
            return {
                'title': 'Error',
                'content': f'Content extraction failed: {str(e)}',
                'links': [],
                'url': self.driver.current_url if self.driver else 'unknown'
            }

    def extract_raw_dom(self):
        """Extract raw HTML DOM for Python-side processing"""
        try:
            # Get the complete page source
            html_source = self.driver.page_source

            # Also get form-specific data via JavaScript
            form_data = self.driver.execute_script("""
                const forms = Array.from(document.querySelectorAll('form'));
                const inputs = Array.from(document.querySelectorAll('input, textarea, select'));

                return {
                    formCount: forms.length,
                    inputCount: inputs.length,
                    visibleTextLength: (document.body.innerText || '').trim().length,
                    forms: forms.map(form => ({
                        action: form.action || '',
                        method: form.method || 'GET',
                        inputs: Array.from(form.querySelectorAll('input, textarea, select')).map(input => ({
                            name: input.name || '',
                            type: input.type || 'text',
                            value: input.value || '',
                            placeholder: input.placeholder || '',
                            id: input.id || ''
                        }))
                    })),
                    allInputs: inputs.map(input => ({
                        name: input.name || '',
                        type: input.type || 'text',
                        value: input.value || '',
                        placeholder: input.placeholder || '',
                        id: input.id || '',
                        inForm: !!input.closest('form')
                    }))
                };
            """)

            return {
                'html_source': html_source,
                'form_data': form_data,
                'url': self.driver.current_url,
                'title': self.driver.title
            }

        except Exception as e:
            logger.error(f"Failed to extract raw DOM: {e}")
            return None

    def process_dom_python_side(self, raw_dom_data):
        """Process DOM content on Python side with full control and debugging"""
        if not raw_dom_data:
            return None

        html_source = raw_dom_data['html_source']
        form_data = raw_dom_data['form_data']
        url = raw_dom_data['url']
        title = raw_dom_data['title']

        # Debug form detection
        logger.debug(f"🔧 Python-side DOM processing: {form_data['formCount']} forms, {form_data['inputCount']} inputs")

        # Check if this should be treated as a form-centric page.
        # Use visible text length (innerText) rather than HTML source size — search pages
        # like Google have huge HTML but almost no visible text, while a news site with a
        # search box in the header has thousands of visible characters of article content.
        visible_text_length = form_data.get('visibleTextLength', 999999)
        is_form_page = (
            form_data['formCount'] > 0 and
            visible_text_length < 2000  # Short visible content = form is the main feature
        ) or (
            form_data['inputCount'] >= 2 and
            any('password' in inp.get('type', '').lower() for inp in form_data['allInputs'])
        )

        logger.debug(f"🔧 Form page detected: {is_form_page}")

        if is_form_page:
            # For form pages, preserve the original HTML structure with forms
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_source, 'html.parser')

            # Import proxy base URL from proxy server
            from src import proxy_server
            PROXY_BASE_URL = proxy_server.PROXY_BASE_URL

            # Fix relative form action URLs to submit through proxy
            base_url = url
            for form in soup.find_all('form'):
                if form.get('action'):
                    action = form['action']
                    if not action.startswith(('http://', 'https://', '/', '//')):
                        # Convert relative URL to absolute for the target
                        absolute_action = urljoin(base_url, action)
                        # But make the form submit to our proxy with the target URL encoded
                        proxy_action = f"{PROXY_BASE_URL}/form-submit?target={quote(absolute_action)}"
                        form['action'] = proxy_action
                        logger.debug(f"🔧 Fixed form action: '{action}' -> '{proxy_action}' (will forward to {absolute_action})")

            # Extract meaningful content while preserving forms
            content_text = soup.get_text(separator=' ', strip=True)

            # Get all links
            links = []
            for link in soup.find_all('a', href=True):
                link_text = link.get_text(strip=True)
                if link_text and link.get('href'):
                    links.append({
                        'text': link_text,
                        'url': link['href']
                    })

            return {
                'title': title,
                'content': content_text,
                'htmlContent': str(soup.body) if soup.body else html_source,
                'links': links,
                'url': url,
                'extraction': {
                    'method': 'python_dom_processing',
                    'confidence': 0.8,
                    'source': 'Python-side DOM extraction with form preservation',
                    'contentLength': len(content_text),
                    'linkCount': len(links),
                    'isFormPage': is_form_page,
                    'formCount': form_data['formCount'],
                    'inputCount': form_data['inputCount']
                }
            }

        return None  # Fall back to JavaScript extraction for non-form pages

    def set_content_filter(self, filter_level):
        """Update the current content filter level"""
        valid_filters = ['minimal', 'balanced', 'all']
        if filter_level in valid_filters:
            self.current_content_filter = filter_level
            logger.info(f"Content filter updated to: {filter_level}")
        else:
            logger.warning(f"Invalid filter level: {filter_level}")

    def close(self):
        """Close Firefox and clean up temporary profiles"""
        if self.driver:
            # Get the temp profile path before quitting if we're in private mode
            if self.use_private_profile and self.temp_profile_path is None:
                try:
                    # Try to get the profile directory from the driver
                    self.temp_profile_path = self.driver.capabilities.get('moz:profile')
                except Exception as e:
                    logger.debug(f"Could not read temporary profile path during close: {e}")

            # Attempt graceful shutdown, but don't fail if connection is already broken
            try:
                # Temporarily suppress urllib3 retry warnings during shutdown
                urllib3_logger = logging.getLogger('urllib3.connectionpool')
                original_level = urllib3_logger.level
                urllib3_logger.setLevel(logging.ERROR)

                self.driver.quit()

                # Restore original logging level
                urllib3_logger.setLevel(original_level)
            except Exception as e:
                # Firefox may have already terminated or connection lost - this is normal
                logger.debug(f"Firefox driver shutdown: {type(e).__name__} - {e}")
                # Still try to clean up the driver object
                self.driver = None
                # Restore original logging level even if exception occurred
                try:
                    urllib3_logger.setLevel(original_level)
                except Exception:
                    pass

            # Clean up temporary profile directory if in private mode
            if self.use_private_profile and self.temp_profile_path:
                try:
                    if os.path.exists(self.temp_profile_path):
                        shutil.rmtree(self.temp_profile_path)
                        logger.debug(f"Cleaned up temporary profile: {self.temp_profile_path}")
                except Exception as e:
                    logger.error(f"PRIVACY BREACH: Failed to clean up temporary profile {self.temp_profile_path}: {e}")
                    logger.error("Manual cleanup required to protect privacy!")

    def _cleanup_temp_profile(self):
        """Clean up temporary profile (called by atexit)"""
        if self.use_private_profile and self.temp_profile_path:
            try:
                if os.path.exists(self.temp_profile_path):
                    shutil.rmtree(self.temp_profile_path)
                    logger.debug(f"atexit: Cleaned up temporary profile: {self.temp_profile_path}")
            except Exception as e:
                logger.error(f"atexit: PRIVACY BREACH: Failed to clean up temporary profile {self.temp_profile_path}: {e}")
                logger.error("Manual cleanup required to protect privacy!")