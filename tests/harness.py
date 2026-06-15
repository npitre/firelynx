"""
Test harness for the Firelynx fixture suite.

Starts, once per test run:
- a local HTTP server serving tests/fixtures/ (so pages load over http://
  exactly like real sites, not file://), and
- a full FirefoxProxy with a PRIVATE Firefox profile (never the user's
  persistent firelynx-profile).

Pages are then fetched the same way lynx fetches them: an HTTP request with
an absolute URL sent to the proxy port. Interaction tests POST to the proxy's
internal endpoints (/modal-action, ...) exactly like a submitted lynx form.

The proxy's HTML response is what lynx receives; `lynx_dump()` renders it to
the text a braille display would show, for content-level assertions.
"""

import atexit
import http.client
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlencode

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
FIXTURES_DIR = os.path.join(TESTS_DIR, 'fixtures')

sys.path.insert(0, REPO_ROOT)


class _QuietFixtureHandler(SimpleHTTPRequestHandler):
    """Serves tests/fixtures/ without per-request logging noise.

    Also accepts POST: returns an echo page reflecting non-sensitive submitted
    fields, so form-submission can be exercised end to end (the page Firefox
    lands on after submitting a fixture form).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FIXTURES_DIR, **kwargs)

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        from urllib.parse import parse_qs
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8', 'replace') if length else ''
        fields = parse_qs(body)
        user = fields.get('user', [''])[0]
        # Never echo password-like fields
        page = ('<!doctype html><html><head><title>Submission received</title>'
                '</head><body><h1>Submission received</h1>'
                f'<p>ECHO-MARKER user={user}</p></body></html>')
        data = page.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Harness:
    """Shared fixture server + Firefox proxy for the whole test run.

    Firefox startup dominates runtime, so a single instance is shared by all
    tests via Harness.get(). Each fetch navigates Firefox to a new page, so
    tests stay independent as long as each one fetches its own fixture.
    """

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.start()
            atexit.register(cls._instance.stop)
        return cls._instance

    def start(self):
        self.fixture_server = ThreadingHTTPServer(('localhost', 0), _QuietFixtureHandler)
        self.fixture_port = self.fixture_server.server_address[1]
        threading.Thread(target=self.fixture_server.serve_forever, daemon=True).start()

        from src.proxy_server import FirefoxProxy
        # Port 8480: away from the defaults (8080/8394) so the suite can run
        # alongside a live firelynx session. Private profile: tests must never
        # touch the user's cookies/sessions.
        self.proxy = FirefoxProxy(port=8480, use_private_profile=True)
        self.proxy.start()
        self.proxy_port = self.proxy.port
        self.proxy_base = f'http://localhost:{self.proxy_port}'

    def stop(self):
        if getattr(self, 'proxy', None):
            self.proxy.stop()
            self.proxy = None
        if getattr(self, 'fixture_server', None):
            self.fixture_server.shutdown()
            self.fixture_server.server_close()
            self.fixture_server = None

    def fixture_url(self, name):
        return f'http://localhost:{self.fixture_port}/{name}'

    def fetch_fixture(self, name):
        """Load a fixture through the proxy; returns the HTML lynx would receive."""
        return self._proxy_request('GET', self.fixture_url(name))

    def fetch_fixture_with_filter(self, name, level):
        """Load a fixture at a specific content-filter level (minimal/balanced/all)."""
        self.proxy.firefox_backend.set_content_filter(level)
        try:
            return self.fetch_fixture(name)
        finally:
            self.proxy.firefox_backend.set_content_filter('balanced')

    def activatable_controls(self, page_html):
        """Labels of in-content controls rendered as activatable lynx links."""
        from bs4 import BeautifulSoup
        labels = []
        for a in BeautifulSoup(page_html, 'html.parser').find_all('a'):
            if 'click-control' in (a.get('href') or ''):
                labels.append(a.get_text(strip=True).strip('[]'))
        return labels

    def click_control(self, page_html, label):
        """Follow the activatable-control link labelled `label`, like lynx does."""
        from bs4 import BeautifulSoup
        for a in BeautifulSoup(page_html, 'html.parser').find_all('a'):
            href = a.get('href') or ''
            if 'click-control' in href and a.get_text(strip=True).strip('[]') == label:
                return self._proxy_request('GET', href)
        raise AssertionError(
            f'No activatable control {label!r}; found: {self.activatable_controls(page_html)}')

    def submit_form(self, fixture_name, fields, find_field):
        """Submit a fixture form the way lynx does: fetch it, read the form
        (identified by a field name) action, and POST the given fields there."""
        from bs4 import BeautifulSoup
        page = self.fetch_fixture(fixture_name)
        soup = BeautifulSoup(page, 'html.parser')
        form = next(f for f in soup.find_all('form')
                    if f.find(attrs={'name': find_field}))
        return self._proxy_request('POST', form.get('action'), body=urlencode(fields))

    def load_more(self, page_html):
        """Follow the [Load more posts] feed-pagination link, like lynx does."""
        from bs4 import BeautifulSoup
        for a in BeautifulSoup(page_html, 'html.parser').find_all('a'):
            if 'load-more' in (a.get('href') or ''):
                return self._proxy_request('GET', a.get('href'))
        raise AssertionError('No [Load more posts] link found')

    def submit_login(self, fixture_name, user, password):
        """Submit a login-shaped form the way lynx does.

        Fetches the fixture, reads the password form's rewritten action (the
        proxy's plain-HTTP /form-submit?target=... endpoint), and POSTs the
        credentials there — exactly what lynx submits. Returns the final HTML
        after following the proxy's redirect/poll dance.
        """
        from bs4 import BeautifulSoup
        page = self.fetch_fixture(fixture_name)
        soup = BeautifulSoup(page, 'html.parser')
        form = next(f for f in soup.find_all('form')
                    if f.find('input', attrs={'type': 'password'}))
        action = form.get('action')
        body = urlencode({'user': user, 'pass': password})
        return self._proxy_request('POST', action, body=body)

    def click_modal_button(self, page_html, label):
        """Submit the converted modal form button labelled `label`.

        Reproduces exactly what lynx sends when the user activates one of the
        '[Label]' submit buttons that modal conversion injected into the page.
        Returns the HTML of the resulting page.
        """
        buttons = self.modal_buttons(page_html)
        if label not in buttons:
            raise AssertionError(
                f'No modal button labelled {label!r} on page; found: {sorted(buttons)}')
        body = urlencode({buttons[label]: f'[{label}]'})
        return self._proxy_request('POST', f'{self.proxy_base}/modal-action', body=body)

    @staticmethod
    def modal_buttons(page_html):
        """Map of button label -> form input name for converted modal buttons.

        Modal conversion encodes each button as
        <input type="submit" name="action|element_id" value="[Label]">.
        Parsed with BeautifulSoup because the pipeline re-serializes HTML and
        attribute order is not stable.
        """
        from bs4 import BeautifulSoup
        buttons = {}
        for inp in BeautifulSoup(page_html, 'html.parser').find_all(
                'input', attrs={'type': 'submit'}):
            name = inp.get('name') or ''
            value = inp.get('value') or ''
            if '|' in name and value.startswith('[') and value.endswith(']'):
                buttons[value[1:-1]] = name
        return buttons

    def _proxy_request(self, method, url, body=None):
        headers = {}
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        # Generous timeout: a fetch includes a full Firefox render + extraction.
        # Limit covers the form-submit dance: immediate 302 -> check-result
        # (may redirect to itself while polling) -> 302 to the real URL -> GET.
        for _ in range(10):  # follow proxy-internal redirects (cache_and_redirect)
            conn = http.client.HTTPConnection('localhost', self.proxy_port, timeout=120)
            try:
                conn.request(method, url, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read().decode('utf-8', 'replace')
            finally:
                conn.close()
            if resp.status in (301, 302) and resp.getheader('Location'):
                url = resp.getheader('Location')
                method, body, headers = 'GET', None, {}
                continue
            return data
        raise AssertionError(f'Too many redirects fetching {url}')

    @staticmethod
    def lynx_dump(page_html):
        """Render proxy HTML to the text lynx would show on a braille display."""
        result = subprocess.run(
            ['lynx', '-dump', '-stdin', '-force_html'],
            input=page_html, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f'lynx -dump failed: {result.stderr}')
        return result.stdout
