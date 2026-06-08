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

logger = logging.getLogger(__name__)

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
            // Comprehensive MFA detection using actual DOM state
            const mfaAnalysis = {
                hasMfaFields: false,
                hasWaitingState: false,
                mfaType: 'none',
                indicators: []
            };

            // 1. Check for MFA-specific form fields (be specific to avoid CSRF/session tokens)
            const mfaFieldSelectors = [
                'input[name*="code"]', 'input[name*="otp"]',
                'input[name*="mfa_token"]', 'input[name*="auth_token"]', 'input[name*="verification_token"]',
                'input[name*="approvals"]', 'input[name*="verification"]',
                'input[id*="code"]', 'input[id*="otp"]'
            ];

            for (const selector of mfaFieldSelectors) {
                if (document.querySelector(selector)) {
                    mfaAnalysis.hasMfaFields = true;
                    mfaAnalysis.mfaType = 'code_entry';
                    mfaAnalysis.indicators.push('MFA input: ' + selector);
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

                if (isLoginPage && (passwordInput || emailInput)) {
                    // We're back on login page - could be waiting for push approval
                    mfaAnalysis.hasWaitingState = true;
                    mfaAnalysis.mfaType = 'facebook_push_pending';
                    mfaAnalysis.indicators.push('Back on login page - likely waiting for push approval');
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

            # Execute the DOM analysis in Firefox
            dom_result = self.driver.execute_script(mfa_analysis_js)

            # Use only DOM analysis - JavaScript patterns too prone to false positives
            if dom_result and (dom_result.get('hasMfaFields') or dom_result.get('hasWaitingState')):
                mfa_type = dom_result.get('mfaType', 'unknown')
                indicators = dom_result.get('indicators', [])
                logger.debug(f"🔐 DOM analysis - Type={mfa_type}")
                for indicator in indicators:
                    logger.debug(f"  - {indicator}")

                logger.info(f"🔐 MFA DETECTED via: DOM analysis")
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

            # Try to find and fill the form
            form_filled = False

            # Look for forms on the page
            forms = self.driver.find_elements(By.TAG_NAME, 'form')

            for form in forms:
                try:
                    # Fill in the form fields
                    for field_name, values in form_data.items():
                        if values:
                            value = values[0]  # Take the first value

                            # Try to find input field by name
                            try:
                                input_field = form.find_element(By.NAME, field_name)
                                input_field.clear()
                                input_field.send_keys(value)
                                safe_value = self.filter_field_value(field_name, value)
                                logger.debug(f"Filled field {field_name} with: {safe_value}")
                            except Exception:
                                # Try by id
                                try:
                                    input_field = form.find_element(By.ID, field_name)
                                    input_field.clear()
                                    input_field.send_keys(value)
                                    safe_value = self.filter_field_value(field_name, value)
                                    logger.debug(f"Filled field {field_name} (by ID) with: {safe_value}")
                                except Exception:
                                    logger.warning(f"Could not find field: {field_name}")

                    # Submit the form
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

            # Quick check if page has started loading, don't wait long
            try:
                # Just a brief moment to see if page starts changing
                time.sleep(1)
                logger.info("Getting page response quickly to avoid lynx timeout")
            except Exception:
                pass

            # Extract the result page
            return self.firefox_backend.extract_page_data()

        except Exception as e:
            logger.error(f"Form submission error: {e}")
            # Fallback to regular page fetch
            return self.firefox_backend.fetch_page(url)

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

            for i, modal in enumerate(modals):
                logger.info(f"🔧 Modal {i+1}: buttonCount={modal.get('buttonCount', 0)}, elementId='{modal.get('elementId', 'NO_ID')}')")

            if not buttons and not modals:
                return page_data

            # Get proxy base URL
            PROXY_BASE_URL = self.get_proxy_base_url()

            # Generate modal interface HTML - use single form with multiple buttons like filter buttons
            modal_html = '''
            <div style="border: 2px solid blue; padding: 15px; margin: 10px; background: #f0f8ff;">
                <h3>🔵 INTERACTIVE ELEMENTS DETECTED</h3>
                <p>This page has buttons or dialogs that need interaction:</p>
                <strong>Actions:</strong> '''

            # Create single form with all buttons
            if buttons or modals:
                modal_html += f'<form method="post" action="{PROXY_BASE_URL}/modal-action" style="display: inline; margin: 0;">'

                # Add buttons to single form - encode action and element_id in button name
                for button in buttons:
                    element_id = button.get('elementId', '')
                    text = button.get('text', 'Button')
                    action = button.get('action', 'click_button')

                    # Encode button info in the submit button name: action|element_id
                    button_name = f"{action}|{element_id}"
                    modal_html += f'<input type="submit" name="{html.escape(button_name)}" value="[{html.escape(text)}]" style="padding: 8px 16px; font-size: 14px; background: #4267B2; color: white; border: none; margin-right: 4px;">'
                    logger.info(f"🔧 Generated button for '{text}': [button value='[{text}]']")

                # Add modal dialogs to single form
                for modal in modals:
                    element_id = modal.get('elementId', '')
                    button_count = modal.get('buttonCount', 0)

                    button_name = f"click_dialog|{element_id}"
                    modal_html += f'<input type="submit" name="{html.escape(button_name)}" value="Open Dialog ({button_count} options)" style="padding: 8px 16px; font-size: 14px; background: #2E8B57; color: white; border: none; margin-right: 4px;">'

                modal_html += '</form><br>'

            modal_html += '''
                <p><em>Click the buttons above to interact with the page elements.</em></p>
            </div>
            '''

            # Inject the modal interface at the beginning of both content and htmlContent
            current_content = page_data.get('content', '')
            current_html_content = page_data.get('htmlContent', '')

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