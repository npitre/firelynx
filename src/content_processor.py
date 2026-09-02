#!/usr/bin/env python3
"""
Content Processor Module
Handles content filtering, HTML generation, and link processing
"""

import re
import html
import logging

from .utils.javascript_loader import load_js_file

logger = logging.getLogger(__name__)


# Input types that do NOT accept a typed value (so they aren't given synthetic
# names or counted when filling form fields positionally).
_NON_FILLABLE_INPUT_TYPES = {
    'hidden', 'submit', 'button', 'checkbox', 'radio', 'image', 'reset', 'file'
}


def _is_fillable_field(tag):
    """True for a form field that accepts a typed value (text/password/etc.,
    textarea, select) - used to name unnamed fields and to match them
    positionally at submit time."""
    if tag.name in ('textarea', 'select'):
        return True
    if tag.name == 'input':
        return (tag.get('type') or 'text').lower() not in _NON_FILLABLE_INPUT_TYPES
    return False


def apply_assistive_semantics(page_data):
    """Post-process extracted HTML with the page's assistive-technology semantics.

    The JavaScript extraction layer already skips assistive-hidden *containers*,
    but selected content can still carry hidden *descendants* (decorative
    icons, app shells inside a kept section). This pass applies the same
    screen-reader contract to the final HTML lynx will see:

    1. Remove subtrees marked aria-hidden="true" or inert - the page itself
       declares them irrelevant to assistive technology. Guarded: if removal
       would leave almost nothing (broken markup hiding everything), keep the
       original so the user never gets a blank page.
    2. Give text-less links their accessible name (aria-label) as visible
       text, so icon links render as words in lynx instead of disappearing.
    """
    html_content = page_data.get('htmlContent', '')
    if not html_content:
        return page_data

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup not available for assistive semantics pass")
        return page_data

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        changed = False

        hidden = soup.select('[aria-hidden="true"], [inert]')
        if hidden:
            total_text = len(soup.get_text(strip=True))
            for element in hidden:
                element.decompose()
            remaining_text = len(soup.get_text(strip=True))
            if remaining_text >= 100 or total_text < 100:
                changed = True
                page_data.setdefault('extraction', {})['assistiveHiddenRemoved'] = len(hidden)
                logger.debug(f"♿ Removed {len(hidden)} assistive-hidden subtrees "
                             f"({total_text - remaining_text} chars)")
            else:
                # Removal would blank the page - keep the original markup
                soup = BeautifulSoup(html_content, 'html.parser')
                logger.debug("♿ Skipped assistive-hidden removal (would leave no content)")

        labelled = 0
        for link in soup.find_all('a'):
            aria_label = (link.get('aria-label') or '').strip()
            if aria_label and not link.get_text(strip=True):
                link.string = aria_label
                labelled += 1
        if labelled:
            changed = True
            logger.debug(f"♿ Labelled {labelled} text-less links from aria-label")

        # Firefox executed the page's JavaScript, so <noscript> fallbacks are
        # wrong here - and lynx renders them (often a meta-refresh redirect to
        # a degraded no-JS variant, e.g. Facebook's _fb_noscript)
        noscript_junk = soup.find_all('noscript') + soup.find_all(
            'meta', attrs={'http-equiv': re.compile(r'^refresh$', re.IGNORECASE)})
        for element in noscript_junk:
            element.decompose()
        if noscript_junk:
            changed = True
            logger.debug(f"🧹 Removed {len(noscript_junk)} noscript/meta-refresh elements")

        if changed:
            page_data['htmlContent'] = str(soup)

    except Exception as e:
        logger.warning(f"Assistive semantics pass failed: {e}")

    return page_data


class ContentProcessor:
    def __init__(self, firefox_backend, show_search_form=False):
        self.firefox_backend = firefox_backend
        self.show_search_form = show_search_form

    def create_lynx_html(self, page_data, original_url):
        """Create clean HTML optimized for lynx display with inline links"""
        return self.generate_final_html(page_data, original_url)

    def generate_final_html(self, page_data, original_url=None, filter_level=None):
        """Centralized HTML generation with all common logic"""
        # Use current filter if no filter level specified
        if filter_level is None:
            filter_level = self.firefox_backend.current_content_filter
        title = html.escape(page_data.get('title', 'No Title'))
        html_content = page_data.get('htmlContent', '')
        links = page_data.get('links', [])
        url = page_data.get('url', original_url or '')
        actual_url = page_data.get('actual_url', url)
        display_url = page_data.get('display_url', actual_url or url)
        ssl_info = page_data.get('ssl_info', {})

        # Apply content filtering based on filter level
        page_data = self.apply_content_filter(page_data, filter_level)

        # Refresh content variables after filtering
        html_content = page_data.get('htmlContent', '')
        links = page_data.get('links', [])

        # Process content - use original HTML structure if available
        if html_content:
            content_html = self.make_html_links_clickable(html_content, links)
        else:
            # Fallback to text-based approach if no HTML available
            content = self.clean_text(page_data.get('content', ''))
            content_html = self.make_inline_links_clickable(content, links)

        # Generate redirect notice if applicable
        redirect_notice = ''
        if original_url and actual_url != original_url:
            redirect_notice = f'<em>Redirected from <strong>{html.escape(original_url)}</strong></em><br>\n'
        elif actual_url and actual_url != url and not url.startswith('http://localhost'):
            redirect_notice = f'<strong>Note:</strong> Redirected from original URL to <em>{html.escape(actual_url)}</em><br>\n'

        # Generate SSL status display
        ssl_status = ''
        if ssl_info:
            if ssl_info.get('is_secure'):
                ssl_icon = '🔒'
                ssl_text = 'Secure HTTPS connection'
                if ssl_info.get('mixed_content'):
                    ssl_icon = '⚠️'
                    ssl_text = f'HTTPS with mixed content ({ssl_info.get("mixed_content_count", "unknown")} insecure elements)'
            elif ssl_info.get('secure') == False:
                ssl_icon = '🔓'
                ssl_text = 'Insecure HTTP connection'
            else:
                ssl_icon = '❓'
                ssl_text = 'Unknown security status'

            ssl_status = f'<strong>Security:</strong> {ssl_icon} {ssl_text}<br>'

        # Generate MFA notice based on comprehensive MFA detection
        mfa_notice = ''
        # Import form processor for MFA detection
        from .form_processor import FormProcessor
        form_processor = FormProcessor(self.firefox_backend)
        mfa_detected = form_processor.is_mfa_challenge_page(page_data)
        if mfa_detected:
            # Use JavaScript detection as source of truth - domain only determines UI style
            # MFA is detected via JavaScript patterns (reliable), domain determines message type
            current_url = page_data.get('url', '').lower()
            if 'facebook.com' in current_url:
                mfa_type = 'facebook_push_pending'
            else:
                mfa_type = 'generic'

            # Generate appropriate MFA message based on type
            current_url = page_data.get('url', '')

            # Import proxy base URL from proxy server
            from src import proxy_server
            PROXY_BASE_URL = proxy_server.PROXY_BASE_URL

            if mfa_type == 'facebook_success':
                # Don't show MFA notice - login was successful
                mfa_notice = ''
            elif mfa_type == 'facebook_push_pending':
                # Facebook push notification pending - show Continue button
                mfa_notice = f'''<div style="border: 2px solid blue; padding: 10px; margin: 10px 0;">
<p><strong>📱 FACEBOOK PUSH NOTIFICATION REQUIRED</strong></p>
<p>Facebook needs you to approve this login on your phone.</p>
<p><strong>Steps:</strong></p>
<ol>
<li><strong>Check your phone</strong> for a Facebook notification</li>
<li>Tap <strong>"Approve"</strong> or <strong>"Yes, it's me"</strong></li>
<li>After approving on your phone, click the button below:</li>
</ol>
<form method="get" action="{PROXY_BASE_URL}/mfa-continue" style="margin-top: 15px;">
<input type="hidden" name="url" value="{html.escape(current_url)}">
<input type="submit" value="✓ I've approved - Continue" style="background: #4267B2; color: white; padding: 8px 16px; border: none; font-size: 14px;">
</form>
<p><em>💡 The Continue button will check if Facebook received your approval.</em></p>
</div>
'''
            else:
                # Generic MFA message
                mfa_notice = f'''<div style="border: 2px solid orange; padding: 10px; margin: 10px 0;">
<p><strong>⚠️ MULTI-FACTOR AUTHENTICATION REQUIRED</strong></p>
<p>This page requires additional authentication.</p>
<p><strong>Instructions:</strong></p>
<ul>
<li>Check your phone for approval notifications</li>
<li>Look for code entry fields in the form below</li>
<li>Complete the authentication step and submit</li>
</ul>
</div>
'''

        # Optional search form for proxy interface
        search_form = ''
        if self.show_search_form:
            # Import proxy base URL from proxy server
            from src import proxy_server
            PROXY_BASE_URL = proxy_server.PROXY_BASE_URL

            search_form = f'''
        <form action="{PROXY_BASE_URL}/search" method="get">
            <p>Search:
            <input type="text" name="q" size="40">
            <select name="engine">
                <option value="duckduckgo">DuckDuckGo</option>
                <option value="google">Google</option>
                <option value="bing">Bing</option>
            </select>
            <input type="submit" value="Search">
            </p>
        </form>
        '''

        # Generate content filter selector
        filter_selector = self.generate_filter_selector(filter_level)

        # Activatable JS-driven controls (role=button), gated by filter level
        page_controls = self.render_page_controls(page_data, filter_level)

        # Generate final HTML
        html_template = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta charset="utf-8">
</head>
<body>
<h1>{title}</h1>
<strong>URL:</strong> {html.escape(display_url)}<br>
{redirect_notice}
{ssl_status}
{filter_selector}
{mfa_notice}
{search_form}
{page_controls}
<hr>
{content_html}
</body>
</html>"""

        return html_template

    def generate_filter_selector(self, active_filter):
        """Generate the content filter selector interface for lynx"""
        # Import proxy base URL from proxy server
        from src import proxy_server
        PROXY_BASE_URL = proxy_server.PROXY_BASE_URL

        # Create a single form with multiple submit buttons for better inline layout
        buttons = []
        for filter_level in ['minimal', 'balanced', 'all']:
            if filter_level == active_filter:
                # Active filter - disabled button (non-clickable)
                buttons.append(f'<input type="submit" value="[*{filter_level}*]" disabled style="background: #ddd; color: #666; border: 1px solid #999; padding: 2px 6px; font-size: 12px; margin-right: 4px;">')
            else:
                # Inactive filter - clickable button with filter value
                buttons.append(f'<input type="submit" name="filter" value="[{filter_level}]" style="background: #f8f8f8; color: #333; border: 1px solid #ccc; padding: 2px 6px; font-size: 12px; cursor: pointer; margin-right: 4px;">')

        form_html = f'<form method="post" action="{PROXY_BASE_URL}/filter-change" style="display: inline; margin: 0;">{"".join(buttons)}</form>'
        return f'<strong>Content:</strong> {form_html}<br>'

    def apply_content_filter(self, page_data, filter_level):
        """Apply different content filtering strategies based on filter level"""

        if filter_level == 'minimal':
            # Aggressive filtering - force use of Readability.js only (most restrictive)
            if hasattr(self.firefox_backend, 'driver') and self.firefox_backend.driver:
                try:
                    # Load Readability.js first
                    readability_source = load_js_file('readability.js')

                    minimal_content = self.firefox_backend.driver.execute_script(f"""
                        // === INJECT READABILITY.JS ===
                        {readability_source}

                        // Force minimal extraction using only Readability.js
                        const documentClone = document.cloneNode(true);
                        const reader = new Readability(documentClone, {{
                            debug: false,
                            maxElemsToParse: 0,
                            nbTopCandidates: 1  // Most restrictive - only best candidate
                        }});
                        const article = reader.parse();

                        if (article && article.textContent && article.textContent.length > 100) {{
                            return {{
                                content: article.textContent,
                                htmlContent: article.content,
                                method: 'readability_minimal',
                                confidence: 0.95
                            }};
                        }}
                        return null;
                    """)

                    if minimal_content:
                        page_data['content'] = minimal_content.get('content', page_data.get('content', ''))
                        page_data['htmlContent'] = minimal_content.get('htmlContent', page_data.get('htmlContent', ''))

                        # Extract links from the Readability.js HTML to keep them synchronized
                        html_content = minimal_content.get('htmlContent', '')
                        if html_content:
                            extracted_links = self._extract_links_from_html(html_content)
                            if extracted_links:
                                page_data['links'] = extracted_links
                                logger.debug(f"Extracted {len(extracted_links)} links from minimal content")

                        if 'extraction' not in page_data:
                            page_data['extraction'] = {}
                        page_data['extraction']['method'] = 'readability_minimal'
                        page_data['extraction']['confidence'] = 0.95
                        page_data['extraction']['source'] = 'Readability.js only - minimal filtering'

                        logger.info(f"🔒 Applied 'minimal' filter - Readability.js only ({len(page_data['content'])} chars)")

                except Exception as e:
                    logger.warning(f"Failed to apply minimal filter: {e}")
                    # Fall back to existing content

            return page_data

        elif filter_level == 'balanced':
            # Moderate filtering - current hybrid extraction (default behavior)
            # This uses the existing multi-strategy approach in content-extraction.js
            return page_data

        elif filter_level == 'all':
            # Minimal filtering - show nearly everything Firefox shows
            # Force use of fallback extraction which preserves more content
            if hasattr(self.firefox_backend, 'driver') and self.firefox_backend.driver:
                try:
                    # Get raw page content with minimal processing
                    all_content = self.firefox_backend.driver.execute_script("""
                        // Get all visible content with minimal filtering
                        const body = document.querySelector('body');
                        if (!body) return { content: 'No body element found', htmlContent: '' };

                        // Remove only clearly problematic elements
                        const elementsToHide = body.querySelectorAll('script, style, noscript');
                        const hiddenElements = [];
                        elementsToHide.forEach(el => {
                            hiddenElements.push({ element: el, display: el.style.display });
                            el.style.display = 'none';
                        });

                        // Extract content and HTML
                        const result = {
                            content: body.innerText || body.textContent || '',
                            htmlContent: body.innerHTML || ''
                        };

                        // Restore hidden elements
                        hiddenElements.forEach(item => {
                            item.element.style.display = item.display;
                        });

                        return result;
                    """)

                    # Override with raw content
                    page_data['content'] = all_content.get('content', page_data.get('content', ''))
                    page_data['htmlContent'] = all_content.get('htmlContent', page_data.get('htmlContent', ''))

                    # Update extraction metadata
                    if 'extraction' not in page_data:
                        page_data['extraction'] = {}
                    page_data['extraction']['method'] = 'all_content'
                    page_data['extraction']['confidence'] = 0.99
                    page_data['extraction']['source'] = 'Raw content - minimal filtering'

                    logger.info(f"🔓 Applied 'all' filter - showing raw content ({len(page_data['content'])} chars)")

                except Exception as e:
                    logger.warning(f"Failed to extract raw content for 'all' filter: {e}")
                    # Fall back to existing content
                    pass

            return page_data

        else:
            # Unknown filter level - use default
            logger.warning(f"Unknown filter level: {filter_level}, using 'balanced'")
            return page_data

    def make_html_links_clickable(self, html_content, links):
        """Make links clickable in original HTML while preserving structure"""
        if not html_content:
            return '<p>No content available</p>'

        # Design choice: Keep original HTTPS URLs in links unchanged
        # When lynx clicks HTTPS links, it sends CONNECT requests to our proxy,
        # which we redirect to HTTP GET. This avoids rewriting every link.

        # Convert non-functional buttons to accessible elements
        html_content = self.convert_non_functional_buttons(html_content)

        return html_content

    def render_page_controls(self, page_data, filter_level):
        """Render JS-driven controls (role=button) the extractor captured as an
        activatable section for lynx, gated by the content filter:

          - minimal:  nothing (reader mode) - only blocking dialogs surface
          - balanced: controls inside the main content landmark
          - all:      every captured control

        Each becomes a link to /click-control, which clicks the real element in
        Firefox and re-extracts. We render from the structured `pageControls`
        list (captured synchronously at extraction) rather than from the HTML,
        because app pages (the Facebook feed) re-render and wipe inline markers,
        and Readability-extracted HTML drops the controls entirely.
        """
        all_controls = page_data.get('pageControls') or []
        # Diagnostic: what the extractor captured (essential for screens we
        # can't reproduce offline, e.g. Facebook's device-trust interstitial)
        if all_controls:
            main_n = sum(1 for c in all_controls if c.get('main'))
            logger.info(f"⚡ Page controls captured: {len(all_controls)} "
                        f"({main_n} in main) - {[c.get('name','') for c in all_controls][:20]}")

        if filter_level == 'minimal':
            return ''

        controls = all_controls
        if filter_level == 'balanced':
            main_controls = [c for c in controls if c.get('main')]
            # If the page has a main landmark, show its controls; if NONE are in
            # main (a landmark-less interstitial like the device-trust screen),
            # "main-only" is meaningless - the controls ARE the page, so show them
            controls = main_controls if main_controls else controls
        if not controls:
            return ''

        from src import proxy_server
        PROXY_BASE_URL = proxy_server.PROXY_BASE_URL
        from urllib.parse import quote

        links = []
        for c in controls:
            name = (c.get('name') or '').strip()
            cid = c.get('id', '')
            if not name:
                continue
            href = f"{PROXY_BASE_URL}/click-control?id={quote(cid)}&name={quote(name)}"
            links.append(f'<a href="{html.escape(href)}">[{html.escape(name)}]</a>')
        if not links:
            return ''

        return ('<div style="border:1px solid #888; padding:8px; margin:8px 0;">'
                '<strong>Page actions:</strong> ' + ' '.join(links) + '</div>')

    def convert_non_functional_buttons(self, html_content):
        """
        Convert non-functional button elements into accessible alternatives for lynx.

        Handles common button patterns that lynx renders as non-functional "script buttons":
        - Dropdown menu toggle buttons: Converts to section headers showing menu is expanded
        - Buttons without actions: Removes them if they have no meaningful href or onclick

        Args:
            html_content: Raw HTML string from page extraction

        Returns:
            Modified HTML with buttons converted to lynx-accessible elements
        """
        try:
            from bs4 import BeautifulSoup, NavigableString
        except ImportError:
            logger.warning("BeautifulSoup not available for button conversion")
            return html_content

        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Give unnamed form fields synthetic names so lynx will serialize
            # and POST the values the user types. JS-app forms (Vue/React) bind
            # inputs with no `name` attribute, so lynx would otherwise send
            # nothing. The name encodes the field's position among fillable
            # fields; submit_form fills the matching field positionally.
            for form in soup.find_all('form'):
                idx = 0
                for field in form.find_all(['input', 'textarea', 'select']):
                    if not _is_fillable_field(field):
                        continue
                    if not field.get('name'):
                        field['name'] = f'fxfield-{idx}'
                    idx += 1

            # Find all button elements that don't have href attributes (non-functional in lynx)
            buttons = soup.find_all('button')

            for button in buttons:
                button_text = button.get_text(strip=True)
                in_form = button.find_parent('form') is not None

                # A <button> defaults to type="submit". Submit-capable buttons
                # inside forms must stay submittable: convert to <input
                # type="submit"> which lynx understands. The label becomes the
                # value; the real submission is Firefox clicking the page's own
                # button, so the exact submitted value does not matter.
                button_type = (button.get('type') or 'submit').lower()
                if button_type == 'submit' and in_form:
                    replacement = soup.new_tag('input')
                    replacement['type'] = 'submit'
                    replacement['value'] = button_text or 'Submit'
                    # A labeled submit <button> may be JS-driven (single-page-app
                    # logins whose native submit does nothing on its own). Carry
                    # the label as fxsubmit so submit_form clicks the page's own
                    # button in Firefox, firing its handler; a genuinely native
                    # submit still works when clicked. Label-less ones keep any
                    # real name for plain Enter-key submission.
                    if button_text:
                        replacement['name'] = 'fxsubmit'
                    elif button.get('name'):
                        replacement['name'] = button['name']
                    button.replace_with(replacement)
                    continue

                # A JS-driven button (type="button") inside a form is how
                # single-page apps submit (a click handler, no native submit).
                # Surface it as a real submit whose name carries the label, so
                # lynx sends the field values and submit_form knows which button
                # to click in Firefox. Label-less ones (icon toggles) fall
                # through to be dropped below.
                if button_type == 'button' and in_form and button_text:
                    replacement = soup.new_tag('input')
                    replacement['type'] = 'submit'
                    replacement['name'] = 'fxsubmit'
                    replacement['value'] = button_text
                    button.replace_with(replacement)
                    continue

                # Check if button is a dropdown/menu toggle
                # Common patterns: aria-expanded, js-details-target, classes with "toggle" or "dropdown"
                is_dropdown_toggle = (
                    button.get('aria-expanded') is not None or
                    'js-details-target' in button.get('class', []) or
                    any('toggle' in str(cls).lower() or 'dropdown' in str(cls).lower()
                        for cls in button.get('class', []))
                )

                if is_dropdown_toggle:
                    # Remove dropdown toggle buttons entirely
                    # The dropdown content is already accessible in lynx without needing the toggle
                    button.decompose()
                else:
                    # For other non-functional buttons, just show as plain text if they have content
                    if button_text:
                        button.replace_with(button_text)
                    else:
                        # Remove empty buttons entirely
                        button.decompose()

            # Ensure every form has a LABELED submit control. Modern sites
            # (Facebook among them) pair a hidden unlabeled <input type=submit>
            # (for Enter-key submission) with a styled [role="button"] div that
            # carries the visible label - dead markup in lynx. Promote that
            # div's accessible name to a real submit input. The actual
            # submission is performed by Firefox clicking the page's own
            # controls, so the substitute's name/value don't matter.
            for form in soup.find_all('form'):
                submit_inputs = [inp for inp in form.find_all('input')
                                 if (inp.get('type') or '').lower() == 'submit']
                if any((inp.get('value') or '').strip() for inp in submit_inputs):
                    continue  # already has a labeled submit

                for div_button in form.find_all(attrs={'role': 'button'}):
                    if div_button.name == 'a':
                        continue  # links work in lynx as-is
                    label = ((div_button.get('aria-label') or '').strip() or
                             div_button.get_text(strip=True))
                    if not label:
                        continue
                    replacement = soup.new_tag('input')
                    replacement['type'] = 'submit'
                    replacement['value'] = label
                    div_button.replace_with(replacement)
                    # The unlabeled helpers would render as a bare confusing
                    # "Submit" next to the labeled one - drop them
                    for inp in submit_inputs:
                        inp.decompose()
                    break  # one labeled submit per form is enough

            return str(soup)

        except Exception as e:
            logger.warning(f"Failed to convert non-functional buttons: {e}")
            return html_content

    def make_inline_links_clickable(self, content, links):
        """Convert content to HTML with inline links made clickable"""
        if not content:
            return '<p>No content available</p>'

        # Split into paragraphs (double newline separation)
        paragraphs = content.split('\n\n')
        content_html = ''

        for para in paragraphs:
            if para.strip():
                # Process each paragraph while preserving line breaks within it
                lines = para.strip().split('\n')
                para_lines_html = []

                for line in lines:
                    if line.strip():
                        # Start with HTML-escaped content
                        line_html = html.escape(line.strip())

                        # Make matching link text clickable
                        for link in links:
                            link_text = link['text'].strip()
                            if link_text and len(link_text) > 2:
                                # Keep original URL (HTTPS or HTTP) - lynx handles HTTPS via CONNECT
                                lynx_url = link['url']

                                # Escape the link text for regex, but handle ellipsis specially
                                escaped_link_text = re.escape(link_text)

                                # Replace the text with a clickable link - complex regex handling
                                # Problem: Link text may contain regex special chars, ellipsis, punctuation
                                # Solution: Try different regex patterns with fallbacks
                                # Don't use word boundaries for text with punctuation
                                if link_text.endswith('...') or any(p in link_text for p in '.!?'):
                                    pattern = escaped_link_text
                                else:
                                    pattern = r'\b' + escaped_link_text + r'\b'

                                replacement = f'<a href="{html.escape(lynx_url)}">{html.escape(link_text)}</a>'

                                # Try primary pattern first
                                if re.search(pattern, line_html, flags=re.IGNORECASE):
                                    line_html = re.sub(pattern, replacement, line_html, count=1, flags=re.IGNORECASE)
                                else:
                                    # Fallback: try simpler escaping (handles ellipsis differently)
                                    simple_pattern = re.escape(link_text.replace('...', r'\.\.\.'))
                                    if re.search(simple_pattern, line_html, flags=re.IGNORECASE):
                                        line_html = re.sub(simple_pattern, replacement, line_html, count=1, flags=re.IGNORECASE)

                        para_lines_html.append(line_html)

                # Join lines with <br> to preserve line breaks within paragraphs
                para_html = '<br>'.join(para_lines_html)
                content_html += f'<p>{para_html}</p>\n'

        return content_html

    def clean_text(self, text):
        """Clean text content"""
        if not text:
            return ""

        # Remove excessive whitespace
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)

        # Remove navigation clutter
        lines = text.split('\n')
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            if line and len(line) > 2 and not self.is_clutter(line):
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def is_clutter(self, line):
        """Identify navigation clutter"""
        clutter_patterns = [
            r'^(Home|Menu|Navigation|Search|Login|Sign in|Register)$',
            r'^(Skip to|Jump to)',
            r'^\s*\|\s*$',
            r'^[•·▪▫]\s*$',
            r'^(Copyright|©|\(c\))',
            r'^(Privacy|Terms|Cookie)',
        ]

        for pattern in clutter_patterns:
            if re.match(pattern, line, re.IGNORECASE):
                return True
        return False

    def _extract_links_from_html(self, html_content):
        """Extract links from HTML content using BeautifulSoup"""
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html_content, 'html.parser')
            links = []

            for link in soup.find_all('a', href=True):
                link_text = link.get_text(strip=True)
                href = link.get('href')

                if link_text and href:
                    # Convert relative URLs to absolute if possible
                    if not href.startswith(('http://', 'https://', '//')):
                        # This is a relative URL - we'll need the base URL to make it absolute
                        # For now, keep it as-is, the browser will resolve it
                        pass

                    links.append({
                        'text': link_text,
                        'url': href
                    })

            return links

        except ImportError:
            logger.warning("BeautifulSoup not available for link extraction")
            return []
        except Exception as e:
            logger.warning(f"Failed to extract links from HTML: {e}")
            return []
