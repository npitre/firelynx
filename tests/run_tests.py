#!/usr/bin/env python3
"""
Firelynx fixture test suite.

Run from the repo root:

    python3 tests/run_tests.py            # whole suite
    python3 tests/run_tests.py TestAriaModal   # one class

Each fixture in tests/fixtures/ encodes one GENERIC web pattern (never a
specific site) and is loaded through a real headless Firefox + the real
proxy pipeline - assertions target the HTML/text lynx would actually show.

Tests marked @unittest.expectedFailure document behavior that is known to be
broken; the comment names the capability expected to fix it. Removing the
marker is that capability's definition of done.
"""

import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Harness


class FixtureTest(unittest.TestCase):
    """Base class: lazily boots the shared Firefox/proxy harness."""

    @classmethod
    def setUpClass(cls):
        cls.harness = Harness.get()


class TestArticleExtraction(FixtureTest):
    """Pattern: article with header/nav/footer clutter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('article.html')
        cls.text = cls.harness.lynx_dump(cls.html)

    def test_article_text_present(self):
        self.assertIn('heirloom tomatoes', self.text)
        self.assertIn('deep roots', self.text)

    def test_title_present(self):
        self.assertIn('The Quiet Garden', self.html)

    def test_article_links_preserved(self):
        self.assertIn('/articles/compost', self.html)

    def test_footer_clutter_filtered(self):
        self.assertNotIn('All rights reserved', self.text)


class TestFormPages(FixtureTest):
    """Pattern: form-centric page (login) - fields must survive extraction."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('login-form.html')

    def test_login_fields_preserved(self):
        self.assertIn('name="username"', self.html)
        self.assertIn('type="password"', self.html)

    def test_form_routed_through_proxy_with_absolute_target(self):
        # POST forms route through the proxy's plain-HTTP /form-submit endpoint
        # (avoids an HTTPS POST through ProxySSL), and the relative action
        # ("login") is resolved to an absolute target so lynx never submits to
        # a host-less path (the "https://" bug). Select the login form, not the
        # proxy's injected filter-bar form.
        from bs4 import BeautifulSoup
        from urllib.parse import unquote
        soup = BeautifulSoup(self.html, 'html.parser')
        form = next(f for f in soup.find_all('form')
                    if f.find('input', attrs={'name': 'username'}))
        action = form.get('action', '')
        self.assertIn('/form-submit?target=', action)
        target = unquote(action.split('target=', 1)[1])
        self.assertTrue(target.startswith('http://') or target.startswith('https://'),
                        f'target not absolute: {target!r}')
        self.assertTrue(target.endswith('/login'), target)


class TestFormSubmission(FixtureTest):
    """The form-submission pipeline (login/polling path) must actually work -
    it regressed when submit_form moved to FormProcessor without a delegate."""

    def test_backend_has_submit_form(self):
        # Regression guard: the proxy submits via firefox_backend.submit_form
        from src.firefox_backend import FirefoxBackend
        self.assertTrue(callable(getattr(FirefoxBackend, 'submit_form', None)))

    def test_login_submission_round_trips(self):
        result = self.harness.submit_login('submit-echo.html',
                                           user='alice', password='secret')
        # The submission reached the target and came back rendered - not the
        # "Firefox Proxy Error / https://" failure chain
        self.assertIn('ECHO-MARKER user=alice', result)
        self.assertNotIn('Firefox Proxy Error', result)
        self.assertNotIn('https://<', result)
        self.assertNotIn('Malformed URL', result)


class TestSubmitTimeValidation(FixtureTest):
    """Pattern: page validates on submit (preventDefault + inline error on bad
    input). Live per-keystroke validation can't work through lynx, but
    submit-time validation must: the submit fires the events in Firefox and the
    inline error surfaces to lynx after the round-trip."""

    def test_invalid_input_surfaces_inline_error(self):
        result = self.harness.submit_form('form-validate.html',
                                          {'email': 'notanemail'}, 'email')
        self.assertIn('VALIDATION-ERROR', result)

    def test_valid_input_passes_through(self):
        result = self.harness.submit_form('form-validate.html',
                                          {'email': 'user@example.com'}, 'email')
        self.assertNotIn('VALIDATION-ERROR', result)


class TestJsSubmitForm(FixtureTest):
    """Pattern: single-page-app login (Vue/React) - unnamed fields, no form
    action, submit is a <button type=button> JS handler, plus a label-less icon
    toggle. lynx must get a pressable submit, and the typed values must reach
    the JS handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('js-submit-form.html')

    def test_js_button_becomes_pressable_submit(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html, 'html.parser')
        submits = [inp for inp in soup.find_all('input', attrs={'type': 'submit'})]
        values = [inp.get('value') for inp in submits]
        self.assertIn('Log in', values)
        # The label-less password toggle must NOT have become a submit control
        self.assertNotIn('', [v for v in values if v is not None])
        self.assertNotIn(None, values)

    def test_unnamed_fields_get_synthetic_names(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html, 'html.parser')
        form = next(f for f in soup.find_all('form') if f.find('input', attrs={'type': 'password'}))
        names = [i.get('name') for i in form.find_all('input')
                 if (i.get('type') or 'text') in ('text', 'password')]
        self.assertIn('fxfield-0', names)
        self.assertIn('fxfield-1', names)

    def test_typed_values_reach_js_handler(self):
        result = self.harness.submit_form(
            'js-submit-form.html',
            {'fxfield-0': 'alice', 'fxfield-1': 'secret', 'fxsubmit': 'Log in'},
            'fxfield-1')
        # The JS click handler ran with the values we typed (proves both the
        # positional field fill and the click-the-JS-button-by-label path)
        self.assertIn('LOGGED-IN user=alice passlen=6', result)


class TestButtonLogin(FixtureTest):
    """Pattern: big-social-site login page - <button> submit, noscript
    meta-refresh fallback, decorative aria-hidden chrome."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('button-login.html')

    def test_button_submit_becomes_lynx_submittable(self):
        # lynx cannot activate a <button>; it must be converted to an
        # <input type="submit"> carrying the same label
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.html, 'html.parser')
        submits = [inp for inp in soup.find_all('input', attrs={'type': 'submit'})
                   if inp.get('value') == 'Log in']
        self.assertEqual(len(submits), 1)
        form = submits[0].find_parent('form')
        self.assertIsNotNone(form)
        # POST form routed through the proxy with an absolute target
        from urllib.parse import unquote
        action = form.get('action', '')
        self.assertIn('/form-submit?target=', action)
        target = unquote(action.split('target=', 1)[1])
        self.assertTrue(target.endswith('/login'), target)

    def test_noscript_fallback_removed(self):
        # Firefox ran the page's JavaScript; the no-JS fallback (meta refresh
        # to a degraded variant) must not leak into lynx
        self.assertNotIn('NOSCRIPT-JUNK-MARKER', self.html)
        self.assertNotIn('_noscript=1', self.html)

    def test_no_mfa_false_positive(self):
        # A fresh login page is just a login page - no MFA flow has started
        self.assertNotIn('AUTHENTICATION REQUIRED', self.html)
        self.assertNotIn('PUSH NOTIFICATION REQUIRED', self.html)

    def test_security_status_present(self):
        # The form-page extraction path gets the same enrichment as others
        self.assertIn('Security:', self.html)


class TestRoleButtonLogin(FixtureTest):
    """Pattern: form with a hidden unlabeled submit input and a styled
    div[role=button] carrying the visible label (a common JS login form)."""

    def test_form_gets_labeled_submit(self):
        from bs4 import BeautifulSoup
        html = self.harness.fetch_fixture('role-button-login.html')
        soup = BeautifulSoup(html, 'html.parser')

        form = next(f for f in soup.find_all('form')
                    if f.find('input', attrs={'name': 'pass'}))
        submits = [inp for inp in form.find_all('input')
                   if (inp.get('type') or '').lower() == 'submit']
        # Exactly one submit, labeled with the div's accessible name; the
        # bare unlabeled helper input must be gone
        self.assertEqual([inp.get('value') for inp in submits], ['Sign In'])


class TestAppShellClassification(FixtureTest):
    """Pattern: app shell with a search form + substantial main landmark and
    low total text. Must NOT be raw-dumped as a form page (which buries the
    feed under chrome) - landmark extraction keeps main and omits chrome."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = cls.harness.lynx_dump(cls.harness.fetch_fixture('app-shell.html'))

    def test_main_content_present(self):
        self.assertIn('riverfront restoration', self.text)

    def test_chrome_not_dumped(self):
        # Raw body-dump (the old misclassification) would include these
        self.assertNotIn('SHELL-CHROME-MARKER', self.text)
        self.assertNotIn('FOOTER-CHROME-MARKER', self.text)


class TestLandmarkComposition(FixtureTest):
    """Pattern: landmarked page (banner/nav/main/aside/footer) - output should
    follow the screen-reader view: main first, navigation collapsed below,
    chrome omitted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('landmarked-page.html')
        cls.text = cls.harness.lynx_dump(cls.html)

    def test_main_content_first_nav_collapsed(self):
        self.assertIn('Hand trowel', self.text)
        self.assertIn('Departments', self.text)
        # The product (main landmark) must come before the collapsed nav
        self.assertLess(self.text.index('Garden tools'),
                        self.text.index('Departments'))

    def test_chrome_omitted(self):
        self.assertNotIn('BANNER-JUNK-MARKER', self.text)
        self.assertNotIn('FOOTER-JUNK-MARKER', self.text)
        self.assertNotIn('ASIDE-JUNK-MARKER', self.text)

    def test_icon_link_gets_accessible_name(self):
        # <a aria-label="Shopping cart"> with only a decorative icon inside
        # must render as words, not vanish
        self.assertIn('Shopping cart', self.text)


class TestPageControlActivation(FixtureTest):
    """Pattern: JS-driven role=button controls. Activation breadth follows the
    content filter - minimal=none, balanced=main only, all=everything - and a
    followed control clicks the real element in Firefox."""

    def test_minimal_activates_nothing(self):
        html = self.harness.fetch_fixture_with_filter('page-controls.html', 'minimal')
        self.assertNotIn('/click-control', html)

    def test_balanced_activates_main_only(self):
        html = self.harness.fetch_fixture_with_filter('page-controls.html', 'balanced')
        controls = self.harness.activatable_controls(html)
        self.assertIn('Like post', controls)        # inside main
        self.assertNotIn('Open menu', controls)     # chrome, outside main

    def test_all_activates_chrome_too(self):
        html = self.harness.fetch_fixture_with_filter('page-controls.html', 'all')
        controls = self.harness.activatable_controls(html)
        self.assertIn('Like post', controls)
        self.assertIn('Open menu', controls)

    def test_following_control_clicks_real_element(self):
        html = self.harness.fetch_fixture_with_filter('page-controls.html', 'all')
        after = self.harness.click_control(html, 'Like post')
        self.assertIn('ACTION:liked', after)


class TestInterstitialControls(FixtureTest):
    """Pattern: a landmark-less interstitial (device-trust screen). Its controls
    are the whole page, so they must surface even at the default balanced filter,
    and following one must click the real element."""

    def test_balanced_surfaces_interstitial_controls(self):
        html = self.harness.fetch_fixture_with_filter('device-trust.html', 'balanced')
        controls = self.harness.activatable_controls(html)
        self.assertIn('Faire confiance', controls)
        self.assertIn('Ne pas faire confiance', controls)

    def test_minimal_still_suppresses(self):
        html = self.harness.fetch_fixture_with_filter('device-trust.html', 'minimal')
        self.assertNotIn('/click-control', html)

    def test_choice_clicks_through(self):
        html = self.harness.fetch_fixture_with_filter('device-trust.html', 'balanced')
        after = self.harness.click_control(html, 'Faire confiance')
        self.assertIn('CHOICE:trusted', after)


class TestFeedHarvest(FixtureTest):
    """Pattern: virtualized infinite-scroll feed. Scroll-harvest must accumulate
    more posts than a single snapshot holds, de-dup them, and let [Load more
    posts] extend the feed."""

    @staticmethod
    def _post_numbers(html):
        return set(re.findall(r'<h3>Post (\d+)</h3>', html))

    def test_initial_batch_beyond_first_screen(self):
        html = self.harness.fetch_fixture('infinite-feed.html')
        posts = self._post_numbers(html)
        # More than the 3 present before any scroll → harvest scrolled and loaded more
        self.assertGreaterEqual(len(posts), 6, f'only got posts {sorted(posts)}')

    def test_no_duplicate_posts(self):
        html = self.harness.fetch_fixture('infinite-feed.html')
        nums = re.findall(r'<h3>Post (\d+)</h3>', html)
        self.assertEqual(len(nums), len(set(nums)), 'feed has duplicate posts')

    def test_load_more_accumulates(self):
        html = self.harness.fetch_fixture('infinite-feed.html')
        first = self._post_numbers(html)
        after = self.harness.load_more(html)
        second = self._post_numbers(after)
        self.assertTrue(second - first, 'Load more added no new posts')
        self.assertTrue(first.issubset(second), 'Load more dropped earlier posts')


class TestMfaDetection(FixtureTest):
    """Pattern: generic MFA challenge (code field + prompt text)."""

    def test_mfa_notice_shown(self):
        html = self.harness.fetch_fixture('mfa-code.html')
        self.assertIn('MULTI-FACTOR AUTHENTICATION REQUIRED', html)

    def test_authenticated_page_no_false_positive(self):
        # A logged-in (localized) page with app chrome and a stray promo "code"
        # field must NOT trip any MFA prompt
        html = self.harness.fetch_fixture('authenticated-app.html')
        self.assertNotIn('AUTHENTICATION REQUIRED', html)
        self.assertNotIn('PUSH NOTIFICATION REQUIRED', html)


class TestAriaModal(FixtureTest):
    """Pattern: well-formed ARIA modal, present at page load."""

    def test_modal_detected_and_clickable(self):
        html = self.harness.fetch_fixture('aria-modal.html')

        # The modal must be surfaced as an actionable lynx form.
        self.assertIn('INTERACTIVE ELEMENTS DETECTED', html)
        buttons = self.harness.modal_buttons(html)
        self.assertIn('Accept', buttons)
        self.assertIn('Cancel', buttons)

        # The dialog's prose appears exactly once - in the converted
        # interface - never duplicated into the page content.
        self.assertEqual(html.count('We use cookies to improve'), 1)

        # Activating [Accept] must click the real button in Firefox: the page
        # records the choice and the dismissed modal must be gone from the
        # re-extracted page.
        after = self.harness.click_modal_button(html, 'Accept')
        self.assertIn('Preferences saved', after)
        self.assertNotIn('INTERACTIVE ELEMENTS DETECTED', after)


class TestStyledControlModal(FixtureTest):
    """Pattern: dialog whose real controls are opacity-hidden under styled
    wrappers that carry the visible label (large e-commerce toolkits)."""

    def test_styled_controls_detected_and_clickable(self):
        html = self.harness.fetch_fixture('styled-control-modal.html')
        buttons = self.harness.modal_buttons(html)
        self.assertIn('Dismiss', buttons)
        self.assertIn('Change address', buttons)

        after = self.harness.click_modal_button(html, 'Change address')
        self.assertIn('Notice address change requested', after)


class TestDivOverlay(FixtureTest):
    """Pattern: ARIA-less cookie-consent overlay (geometry + scroll lock only)."""

    def test_overlay_detected(self):
        html = self.harness.fetch_fixture('div-overlay.html')
        buttons = self.harness.modal_buttons(html)
        self.assertIn('Accept cookies', buttons)


class TestLateModal(FixtureTest):
    """Pattern: modal injected ~1s after load (newsletter/consent style)."""

    def test_late_modal_detected(self):
        html = self.harness.fetch_fixture('late-modal.html')
        buttons = self.harness.modal_buttons(html)
        self.assertIn('Subscribe', buttons)
        self.assertIn('No thanks', buttons)


class TestBackgroundHidden(FixtureTest):
    """Pattern: page marks its background aria-hidden while a dialog is open."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.html = cls.harness.fetch_fixture('background-hidden.html')

    def test_modal_buttons_surfaced(self):
        buttons = self.harness.modal_buttons(self.html)
        self.assertIn('Stay signed in', buttons)
        self.assertIn('Log out', buttons)

    def test_dialog_text_shown_once(self):
        self.assertEqual(self.html.count('about to expire due to inactivity'), 1)

    def test_background_suppressed(self):
        # A screen reader user would hear only the dialog; the aria-hidden
        # application content should not drown it out.
        text = self.harness.lynx_dump(self.html)
        self.assertNotIn('BACKGROUND-ONLY-MARKER', text)


class TestRerenderModal(FixtureTest):
    """Pattern: framework re-renders the dialog's DOM nodes continuously."""

    def test_click_survives_rerender(self):
        html = self.harness.fetch_fixture('rerender-modal.html')
        buttons = self.harness.modal_buttons(html)
        self.assertIn('Decline', buttons)

        # A real user takes seconds to read the page before acting; by then
        # the framework has re-rendered and the element reference captured at
        # extraction time is stale. The click must still land on the button
        # the user chose, not whatever is found first.
        time.sleep(1.5)
        after = self.harness.click_modal_button(html, 'Decline')
        self.assertIn('You declined', after)


if __name__ == '__main__':
    unittest.main(verbosity=2)
