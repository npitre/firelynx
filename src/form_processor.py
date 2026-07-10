"""
Form Processing Module for Firelynx

This module handles all form-related processing functionality including:
- Multi-factor authentication (MFA) detection and handling
- Form submission with security filtering
- Modal dialog conversion to accessible forms
- Security functions for sensitive data filtering
"""

import time
import random
import re
import html
import logging
from selenium.webdriver.common.by import By
from urllib.parse import parse_qs, urlencode, quote, urljoin

from .utils.javascript_loader import load_js_file

logger = logging.getLogger(__name__)

# Form fields that accept a typed value, in document order — used to place
# values from synthetic fxfield-N names (JS-app forms have no field names).
# Mirrors content_processor._is_fillable_field.
FILLABLE_FIELD_SELECTOR = (
    "input:not([type='hidden']):not([type='submit']):not([type='button'])"
    ":not([type='checkbox']):not([type='radio']):not([type='image'])"
    ":not([type='reset']):not([type='file']), textarea, select"
)

# Field-name substrings that mark a value as sensitive and unsafe to log.
SENSITIVE_FIELD_PATTERNS = [
    'password', 'passwd', 'pwd', 'pass',
    'secret', 'token', 'key', 'auth',
    'credit', 'card', 'ccv', 'cvv',
    'ssn', 'social'
]


class FormProcessor:
    """
    Handles all form-related processing for the Firelynx accessible browser.

    This class manages form submissions, MFA detection, modal conversion, and
    security filtering for the Firefox backend.
    """

    def __init__(self, firefox_backend):
        """
        Initialize the FormProcessor with a Firefox backend reference.

        Args:
            firefox_backend: The FirefoxBackend instance to use for browser operations
        """
        self.firefox_backend = firefox_backend
        self.driver = firefox_backend.driver

    def get_proxy_base_url(self):
        """Get the proxy base URL from the main module."""
        try:
            # Import here to avoid circular imports
            from src import proxy_server
            return proxy_server.PROXY_BASE_URL
        except (ImportError, AttributeError):
            return None

    def is_mfa_challenge_page(self, page_data):
        """Detect MFA challenge using comprehensive analysis including JavaScript capture"""
        if not page_data:
            logger.debug("MFA detection: No page data")
            return False

        url = page_data.get('url', '').lower()
        logger.debug(f"MFA detection analyzing page for: {url[:100]}")

        try:
            # Analyze the live Firefox DOM for MFA indicators
            mfa_analysis_js = """
            // True when a login form was submitted recently in this session -
            // gates heuristics that are only valid mid-login-flow
            const recentLoginSubmission = arguments[0];

            // Comprehensive MFA detection using actual DOM state
            const mfaAnalysis = {
                hasMfaFields: false,
                hasWaitingState: false,
                mfaType: 'none',
                indicators: []
            };

            // Is an element actually presented to the user? Catches the common
            // ways a control is hidden: display/visibility/opacity, zero size,
            // aria-hidden/hidden, and offscreen positioning. Facebook keeps a
            // HIDDEN input[autocomplete="one-time-code"] (WebOTP autofill) on
            // logged-in pages — it must not count as a credential challenge.
            function fxVisible(el) {
                if (!el) return false;
                if (el.closest('[aria-hidden="true"], [hidden]')) return false;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' ||
                    parseFloat(s.opacity || '1') === 0) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return false;          // zero-size
                const vw = window.innerWidth || 1366, vh = window.innerHeight || 768;
                if (r.bottom < 0 || r.right < 0 || r.left > vw || r.top > vh * 3) {
                    return false;                                       // offscreen
                }
                return true;
            }

            // 0. Authenticated-session override (language-agnostic).
            // If the page shows app chrome you only get AFTER signing in
            // (messages/notifications/friends/logout links) AND has no
            // credential-entry field, it is not an auth challenge — regardless
            // of any stray "code"-named input or security text elsewhere on
            // the page. This replaces the old English-only success-text check
            // ("what is on your mind"), which never matched localized UIs and
            // let logged-in pages trip a false MFA prompt. Skipped while a
            // login was just submitted, so the push-pending flow still runs.
            const hasAuthChrome = !!document.querySelector(
                'a[href*="logout"], a[href*="/messages/"], a[href*="/notifications"], a[href*="/friends/"]');
            // A REAL, VISIBLE credential field — not any input whose name
            // happens to contain "code" (promo/country/CSRF), and not a hidden
            // autofill helper, both of which tripped the false positive
            const hasCredentialField = Array.from(document.querySelectorAll(
                'input[type="password"], input[name="pass"], ' +
                'input[autocomplete="one-time-code"], input[name*="otp"]')).some(fxVisible);
            if (hasAuthChrome && !hasCredentialField && !recentLoginSubmission) {
                mfaAnalysis.mfaType = 'authenticated';
                mfaAnalysis.indicators.push('Authenticated app chrome present, no credential field');
                return mfaAnalysis;  // stays hasMfaFields=false → not an MFA challenge
            }

            // 1. Check for MFA-specific form fields. Specific OTP patterns only —
            // a bare name*="code" also matches promo/country/CSRF fields and was
            // a source of false positives.
            const mfaFieldSelectors = [
                'input[autocomplete="one-time-code"]',
                'input[name*="otp"]', 'input[id*="otp"]',
                'input[name*="mfa"]', 'input[name*="2fa"]',
                'input[name*="approvals_code"]',
                'input[name="verification_code"]', 'input[name="security_code"]',
                'input[name="code"]'
            ];

            for (const selector of mfaFieldSelectors) {
                // Only a VISIBLE code field is an actual challenge — a hidden
                // autofill/WebOTP input does not count
                const visibleField = Array.from(document.querySelectorAll(selector)).find(fxVisible);
                if (visibleField) {
                    mfaAnalysis.hasMfaFields = true;
                    mfaAnalysis.mfaType = 'code_entry';
                    mfaAnalysis.indicators.push('Visible MFA input: ' + selector);
                    break;
                }
            }

            // 2. Facebook-specific: Check for checkpoint/disabled state
            if (window.location.href.includes('facebook.com')) {
                // Check for Facebook checkpoint URLs or form actions
                const forms = document.querySelectorAll('form');
                for (const form of forms) {
                    if (form.action && (
                        form.action.includes('checkpoint') ||
                        form.action.includes('approvals') ||
                        form.action.includes('device-based')
                    )) {
                        mfaAnalysis.hasMfaFields = true;
                        mfaAnalysis.mfaType = 'facebook_checkpoint';
                        mfaAnalysis.indicators.push('FB checkpoint form: ' + form.action);
                        break;
                    }
                }

                // Check if login form fields are disabled (waiting state)
                const passwordInput = document.querySelector('input[name="pass"], input[type="password"]');
                const emailInput = document.querySelector('input[name="email"]');
                if ((passwordInput && passwordInput.disabled) ||
                    (emailInput && emailInput.disabled)) {
                    mfaAnalysis.hasWaitingState = true;
                    mfaAnalysis.mfaType = 'facebook_waiting';
                    mfaAnalysis.indicators.push('Form fields disabled - waiting state');
                }

                // NEW: Check for login success indicators to avoid MFA loop
                // Look for signs that login succeeded and we shouldn't show MFA warning
                // Be very specific to avoid false positives - only actual Facebook app content
                const successIndicators = [
                    'what is on your mind', 'create post', 'whats on your mind'
                ];

                const bodyText = document.body.textContent.toLowerCase();
                const hasSuccessIndicator = successIndicators.some(indicator =>
                    bodyText.includes(indicator)
                );

                if (hasSuccessIndicator) {
                    // Login appears successful - don't trigger MFA detection
                    mfaAnalysis.hasMfaFields = false;
                    mfaAnalysis.hasWaitingState = false;
                    mfaAnalysis.mfaType = 'facebook_success';
                    mfaAnalysis.indicators.push('Login appears successful - found success indicators');
                    return mfaAnalysis; // Early return to skip other MFA checks
                }

                // Check if we're still on login page after a form submission (potential loop)
                const isLoginPage = bodyText.includes('log into facebook') ||
                                   bodyText.includes('log in to facebook') ||
                                   window.location.pathname.includes('/login');

                // Only meaningful AFTER a login attempt: a fresh visit to the
                // login page is just a login page, not a pending approval
                if (isLoginPage && (passwordInput || emailInput) && recentLoginSubmission) {
                    // We're back on login page - could be waiting for push approval
                    mfaAnalysis.hasWaitingState = true;
                    mfaAnalysis.mfaType = 'facebook_push_pending';
                    mfaAnalysis.indicators.push('Back on login page after submission - likely waiting for push approval');
                }
            }

            // 3. Look for common MFA text patterns
            const bodyText = document.body.textContent.toLowerCase();
            const mfaTextPatterns = [
                'enter the code', 'verification code', 'two-factor',
                'approve this login', 'check your phone', 'security check'
            ];

            for (const pattern of mfaTextPatterns) {
                if (bodyText.includes(pattern)) {
                    mfaAnalysis.hasMfaFields = true;
                    mfaAnalysis.mfaType = 'text_mfa';
                    mfaAnalysis.indicators.push('MFA text: ' + pattern);
                    break;
                }
            }

            return mfaAnalysis;
            """

            # Execute the DOM analysis in Firefox. A login submission stays
            # "recent" for 10 minutes - enough for any push-approval flow.
            recent_login = (time.time() - getattr(self.firefox_backend, 'login_submitted_at', 0)) < 600
            dom_result = self.driver.execute_script(mfa_analysis_js, recent_login)

            # Use only DOM analysis - JavaScript patterns too prone to false positives
            if dom_result and (dom_result.get('hasMfaFields') or dom_result.get('hasWaitingState')):
                mfa_type = dom_result.get('mfaType', 'unknown')
                indicators = dom_result.get('indicators', [])
                # INFO level so a --verbose run reveals exactly which signal
                # fired (essential for diagnosing false positives in the wild)
                logger.info(f"🔐 MFA DETECTED via DOM analysis - Type={mfa_type}")
                for indicator in indicators:
                    logger.info(f"  - {indicator}")
                return True

        except Exception as e:
            logger.warning(f"MFA comprehensive analysis failed: {e}")
            # Fallback to basic URL pattern matching
            mfa_url_patterns = ['checkpoint', 'approvals', 'verify', '2fa']
            for pattern in mfa_url_patterns:
                if pattern in url:
                    logger.debug(f"🔐 MFA detected via URL fallback: '{pattern}'")
                    return True

        logger.debug("MFA detection: No indicators found")
        return False

    def submit_form(self, url, post_data, headers):
        """Submit a form using Firefox with proper POST data"""
        try:
            # Parse the POST data

            # Decode POST data
            if isinstance(post_data, bytes):
                post_data_str = post_data.decode('utf-8')
            else:
                post_data_str = post_data

            logger.info(f"Form submission to: {url}")

            # Filter sensitive data for logging
            safe_post_data = self.filter_sensitive_data(post_data_str)
            logger.debug(f"POST data: {safe_post_data}")

            # Navigate to the URL first
            time.sleep(random.uniform(0.3, 0.8))
            self.driver.get(url)
            self.firefox_backend.hide_webdriver_traces()

            # Parse form data
            form_data = parse_qs(post_data_str)

            # Remember login-shaped submissions: the Facebook push-approval
            # detection ("back on the login page = waiting for approval") is
            # only meaningful after one
            if any('pass' in field_name.lower() for field_name in form_data):
                self.firefox_backend.login_submitted_at = time.time()
                logger.debug("🔐 Login-shaped submission recorded (password field present)")

            # The clicked JS-submit button's label travels as fxsubmit (see
            # content_processor); it selects which button to click, not a field.
            fx_submit_label = form_data.pop('fxsubmit', [None])[0]

            # Try to find and fill the form
            form_filled = False

            # Look for forms on the page
            forms = self.driver.find_elements(By.TAG_NAME, 'form')

            for form in forms:
                try:
                    # The live page's fillable fields, in document order — used
                    # to place values from synthetic fxfield-N names (JS-app
                    # forms have no field names of their own).
                    fillable = form.find_elements(By.CSS_SELECTOR, FILLABLE_FIELD_SELECTOR)

                    # Fill in the form fields
                    for field_name, values in form_data.items():
                        if not values:
                            continue
                        value = values[0]  # Take the first value
                        safe_value = self.filter_field_value(field_name, value)

                        # Synthetic positional field (unnamed JS-app input)
                        if field_name.startswith('fxfield-'):
                            try:
                                pos = int(field_name.split('-', 1)[1])
                            except ValueError:
                                continue
                            if 0 <= pos < len(fillable):
                                fillable[pos].clear()
                                fillable[pos].send_keys(value)
                                logger.debug(f"Filled field #{pos} with: {safe_value}")
                            continue

                        # Named field: find by name, then id. Never refill a
                        # hidden field (e.g. a CSRF token the reloaded page set
                        # itself) with the stale posted value.
                        try:
                            input_field = form.find_element(By.NAME, field_name)
                        except Exception:
                            try:
                                input_field = form.find_element(By.ID, field_name)
                            except Exception:
                                logger.warning(f"Could not find field: {field_name}")
                                continue
                        if (input_field.get_attribute('type') or '').lower() == 'hidden':
                            continue
                        input_field.clear()
                        input_field.send_keys(value)
                        logger.debug(f"Filled field {field_name} with: {safe_value}")

                    # Submit. If the user activated a JS-driven button, click
                    # THAT button in Firefox by its label (re-using the modal
                    # click resolver), which fires the page's own handler.
                    if fx_submit_label:
                        try:
                            modal_js = load_js_file('modal-detection.js')
                            result = self.driver.execute_script(
                                modal_js + "\nreturn clickPageControl('', arguments[0]);",
                                fx_submit_label)
                            if result and result.get('success'):
                                logger.info(f"Clicked JS submit button: {fx_submit_label!r}")
                                form_filled = True
                                break
                            logger.warning(f"JS submit button {fx_submit_label!r} not "
                                           f"found, trying generic submit")
                        except Exception as e:
                            logger.warning(f"JS submit click failed: {e}")

                    # Otherwise (or as fallback): click a native submit control
                    try:
                        submit_button = form.find_element(By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"], button:not([type])')
                        logger.info("Clicking submit button...")

                        # Execute the click via JavaScript for faster response
                        self.driver.execute_script("arguments[0].click();", submit_button)
                        logger.info("Form submitted via JavaScript click")
                        form_filled = True
                        break
                    except Exception as e:
                        logger.warning(f"JavaScript click failed: {e}, trying direct submit")
                        # Try submitting the form directly
                        try:
                            form.submit()
                            logger.info("Form submitted directly")
                            form_filled = True
                            break
                        except Exception as e2:
                            logger.error(f"Form submit failed: {e2}")
                            continue

                except Exception as e:
                    logger.error(f"Error filling form: {e}")
                    continue

            if not form_filled:
                logger.warning("No suitable form found, treating as regular GET")
                # Fallback: just navigate to the URL
                pass

            # Wait for the submission result to settle (navigation, MFA UI,
            # validation errors appearing in place) before extracting
            self.firefox_backend.wait_for_page_settle()

            # Extract the result page
            return self.firefox_backend.extract_page_data()

        except Exception as e:
            logger.error(f"Form submission error: {e}")
            # Return a page-data DICT (callers render this, not raw HTML bytes);
            # fetch_page() returns bytes and would corrupt the result pipeline
            safe_url = url if str(url).startswith(('http://', 'https://')) else ''
            return {
                'title': 'Form Submission Error',
                'content': f'Form submission failed: {e}',
                'htmlContent': '',
                'url': safe_url,
                'links': [],
            }

    def convert_modal_elements_to_forms(self, page_data):
        """
        Convert detected modal elements to lynx-friendly HTML forms.

        This method runs on the Python/proxy side and generates forms that submit
        back to the proxy. This is the correct architecture - Firefox only detects
        elements, Python generates the interface for lynx.

        Args:
            page_data: Page data dict containing modalElements from Firefox

        Returns:
            Modified page_data dict with modal forms injected into content
        """
        try:
            modal_elements = page_data.get('modalElements', {})
            buttons = modal_elements.get('buttons', [])
            modals = modal_elements.get('modals', [])

            logger.info(f"🔧 Modal conversion starting: {len(buttons)} buttons, {len(modals)} modals")

            for i, button in enumerate(buttons):
                logger.info(f"🔧 Button {i+1}: text='{button.get('text', 'NO_TEXT')}', action='{button.get('action', 'NO_ACTION')}', elementId='{button.get('elementId', 'NO_ID')}'")

            if not buttons:
                return page_data

            # Get proxy base URL
            PROXY_BASE_URL = self.get_proxy_base_url()

            button_style = ('padding: 8px 16px; font-size: 14px; background: #4267B2; '
                            'color: white; border: none; margin-right: 4px;')

            def render_buttons(dialog_buttons):
                """One submit input per button; name encodes action|element_id,
                the [Label] value carries the accessible name used for click
                re-resolution."""
                parts = []
                for button in dialog_buttons:
                    element_id = button.get('elementId', '')
                    text = button.get('text', 'Button')
                    action = button.get('action', 'click_button')
                    button_name = f"{action}|{element_id}"
                    parts.append(f'<input type="submit" name="{html.escape(button_name)}" '
                                 f'value="[{html.escape(text)}]" style="{button_style}">')
                return ''.join(parts)

            buttons_by_dialog = {}
            for button in buttons:
                buttons_by_dialog.setdefault(button.get('dialogId', ''), []).append(button)

            # One form, one section per dialog: its name (when the page labels
            # it), its text, then its buttons. This is THE place a detected
            # dialog is presented — extraction excludes dialog content from
            # the page body so nothing appears twice.
            modal_html = (
                '<div style="border: 2px solid blue; padding: 15px; margin: 10px; background: #f0f8ff;">\n'
                '<h3>🔵 INTERACTIVE ELEMENTS DETECTED</h3>\n'
                f'<form method="post" action="{PROXY_BASE_URL}/modal-action" style="margin: 0;">'
            )

            rendered_dialogs = set()
            for modal in modals:
                dialog_id = modal.get('elementId', '')
                dialog_buttons = buttons_by_dialog.get(dialog_id)
                if not dialog_buttons:
                    continue
                rendered_dialogs.add(dialog_id)
                name = (modal.get('name') or '').strip()
                text = (modal.get('text') or '').strip()
                # Dialogs often start with their own visible title; don't
                # repeat it when it equals the explicit label
                if name and text.startswith(name):
                    text = text[len(name):].strip()
                if name:
                    modal_html += f'<h4>{html.escape(name)}</h4>'
                if text:
                    modal_html += f'<p>{html.escape(text)}</p>'
                modal_html += '<p>' + render_buttons(dialog_buttons) + '</p>'

            # Defensive: buttons whose dialog entry is missing still get shown
            orphan_buttons = [button
                              for dialog_id, dialog_buttons in buttons_by_dialog.items()
                              if dialog_id not in rendered_dialogs
                              for button in dialog_buttons]
            if orphan_buttons:
                modal_html += '<p>' + render_buttons(orphan_buttons) + '</p>'

            modal_html += '</form>\n</div>\n'

            # Inject the modal interface at the beginning of both content and htmlContent
            current_content = page_data.get('content', '')
            # Dialog markup embedded inside content sections would render the
            # dialog's text a second time - the interface above is canonical
            current_html_content = self._remove_dialog_markup(page_data.get('htmlContent', ''))

            page_data['content'] = modal_html + '\n\n<hr>\n\n' + current_content

            # Always set htmlContent to ensure HTML processing (not text processing)
            # This prevents make_inline_links_clickable() from HTML-escaping our modal HTML
            if current_html_content:
                page_data['htmlContent'] = modal_html + '\n\n<hr>\n\n' + current_html_content
            else:
                # If no htmlContent exists, create it from modal HTML + text content
                # Convert text content to HTML paragraphs
                content_as_html = '<div>' + current_content.replace('\n\n', '</div><div>').replace('\n', '<br>') + '</div>'
                page_data['htmlContent'] = modal_html + '\n\n<hr>\n\n' + content_as_html

            page_data['modalsConverted'] = len(buttons) + len(modals)

            logger.info(f"🔧 Modal conversion complete: {page_data['modalsConverted']} elements converted and injected into content")
            logger.info(f"🔧 Content now starts with: {page_data['content'][:100]}...")

            return page_data

        except Exception as e:
            logger.warning(f"Modal conversion failed: {e}")
            return page_data

    def _remove_dialog_markup(self, html_content):
        """Remove dialog markup embedded in extracted page HTML.

        A detected dialog is presented once, by the converted interface that
        convert_modal_elements_to_forms() injects. Copies of the dialog's
        markup can still sit inside content sections whose source element
        contained the dialog (e.g. a main landmark wrapping it) — and lynx
        ignores CSS, so even display:none dialog markup would render as
        visible text. Strip it all.
        """
        if not html_content:
            return html_content
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            removed = 0
            for element in soup.select(
                    'dialog, [role="dialog"], [role="alertdialog"], '
                    '[aria-modal="true"], [data-modal-id]'):
                element.decompose()
                removed += 1
            if removed:
                logger.debug(f"🔧 Removed {removed} embedded dialog markup copies from content")
                return str(soup)
        except Exception as e:
            logger.debug(f"Dialog markup removal failed: {e}")
        return html_content

    def filter_sensitive_data(self, post_data_str):
        """Filter sensitive information from POST data for logging"""
        if not post_data_str:
            return post_data_str

        try:
            form_data = parse_qs(post_data_str)
            filtered_data = {}

            for field_name, values in form_data.items():
                field_lower = field_name.lower()
                is_sensitive = any(pattern in field_lower for pattern in SENSITIVE_FIELD_PATTERNS)

                if is_sensitive:
                    filtered_data[field_name] = ['[FILTERED]']
                else:
                    filtered_data[field_name] = values

            return urlencode(filtered_data, doseq=True)
        except Exception:
            # If parsing fails, just return a generic message
            return "[POST data filtered for security]"

    def filter_field_value(self, field_name, value):
        """Filter sensitive field values for logging"""
        if not field_name or not value:
            return value

        field_lower = field_name.lower()
        is_sensitive = any(pattern in field_lower for pattern in SENSITIVE_FIELD_PATTERNS)

        if is_sensitive:
            return '[FILTERED]'
        else:
            return value