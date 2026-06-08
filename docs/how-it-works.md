# How Firelynx Works

Firelynx lets you browse the modern, JavaScript-heavy web from `lynx` — keeping
the fast, semantic, braille- and screen-reader-friendly text interface while a
real Firefox does the heavy lifting behind the scenes.

This document explains the architecture at a conceptual level. For day-to-day
usage see the [README](../README.md).

## The big picture

```
  ┌────────┐   HTTP/HTTPS    ┌──────────────────┐   Selenium    ┌─────────┐
  │  lynx  │ ──────────────► │  Firelynx proxy  │ ────────────► │ Firefox │ ──► web
  │ (you)  │ ◄────────────── │  (Python, local) │ ◄──────────── │(headless)│
  └────────┘  clean semantic └──────────────────┘  rendered DOM └─────────┘
                  HTML
```

1. `lynx` is configured to use the Firelynx proxy for both HTTP and HTTPS.
2. When you open a page, the proxy drives a headless Firefox (via Selenium
   WebDriver) to actually load and run the site — including its JavaScript.
3. The proxy extracts the meaningful content from the rendered page and returns
   clean, semantic HTML to `lynx`.
4. `lynx` renders that HTML the way it always has, so your keybindings,
   customizations, and braille workflow are unchanged.

The proxy and Firefox run entirely on your machine; nothing about your browsing
is sent anywhere except to the sites you visit.

## Components

The Python code lives in `src/`:

- **`FirefoxProxy`** (`proxy_server.py`) — owns the lifecycle: starts the local
  HTTP server (stdlib `http.server`), launches the Firefox backend, picks an
  available port (so multiple sessions can run at once), and spawns `lynx` with
  the right proxy environment.
- **`HTTPProxyHandler`** (`proxy_handler.py`) — handles each request from
  `lynx`: GET/POST/CONNECT, plus internal commands (filter changes, modal
  actions, MFA continue, the search form, form submission).
- **`FirefoxBackend`** (`firefox_backend.py`) — manages the Selenium WebDriver,
  loads pages, and runs the content-extraction JavaScript.
- **`ContentProcessor`** (`content_processor.py`) — turns extracted page data
  into the final HTML `lynx` sees, applies the content filter, and makes links
  usable.
- **`FormProcessor`** (`form_processor.py`) — form submission, multi-factor
  authentication detection, and converting JavaScript dialogs into accessible
  forms.

## Content extraction

A rendered modern page is mostly noise for a text browser. Firelynx runs a
layered, **generic** extraction pipeline (in `js/`) and picks the best result by
confidence — no per-site rules:

1. **Mozilla Readability.js** — the same algorithm as Firefox Reader Mode.
2. **Accessibility landmarks** — `main`, `article`, ARIA `role` regions: what a
   screen reader would prioritize.
3. **Interactive elements** — modals and dialogs, surfaced so they're actionable.
4. **Semantic scoring** — heuristic scoring of containers by tag, ARIA role,
   class/id hints, link density, and text length.
5. **Fallback extraction** — a permissive pass when the above filter too
   aggressively (e.g. navigation-dense pages).

If extraction yields too little, the proxy automatically retries with the
fallback pass. The same extraction path is used both for the initial page load
and when you switch content-filter levels, so links stay consistent.

### Content filter levels

Switchable at runtime from within the page (`--content` sets the default):

- **`minimal`** — Reader-mode-style, Readability.js only.
- **`balanced`** — the default hybrid pipeline above.
- **`all`** — show nearly everything the page rendered.

## HTTPS without losing the path (ProxySSL)

A normal HTTP proxy learns the full URL of a request. But for HTTPS, `lynx`
issues an opaque `CONNECT host:443` and then speaks TLS — the proxy never sees
the path. Firelynx solves this with **ProxySSL** (`proxyssl/`), a small
`LD_PRELOAD` shim loaded into `lynx`:

- For connections to the local Firelynx proxy, ProxySSL intercepts the OpenSSL
  calls and *fakes* the TLS handshake, passing the bytes through as plain HTTP.
- `lynx` therefore sends the complete `https://host/path` request in the clear —
  but only ever to `localhost`, never over the network.
- The proxy reads the full URL and hands it to Firefox, which makes the *real*
  TLS connection to the site.

ProxySSL only touches connections to the configured local proxy port
(`PROXYSSL_PORT`); all other TLS traffic uses the real OpenSSL functions
untouched. The library is built and wired in automatically by `install.sh`; the
proxy sets `LD_PRELOAD` on the `lynx` subprocess for you.

**On the ethics:** this is TLS interception, which is normally a red flag. Here
it is strictly local and self-directed — you are intercepting your *own*
traffic to your *own* localhost proxy to make your *own* browser accessible.
No third party is involved and nothing leaves your machine unencrypted.

## Forms, dialogs, and MFA

- **Forms** are filled and submitted in the real Firefox, so JavaScript-driven
  forms work. Because submission can take many seconds, the proxy returns an
  immediate redirect and polls in the background so `lynx` doesn't time out.
- **JavaScript modals/dialogs** are detected generically (ARIA `role="dialog"`,
  `aria-modal`, visibility checks) and converted into plain HTML forms; clicking
  a button tells Firefox to click the corresponding real element.
- **Multi-factor authentication** is detected from the live DOM (code fields,
  waiting states, common prompt text). Firelynx includes a small amount of
  Facebook-specific handling for its phone-approval push flow — the one place
  generic detection isn't enough — which surfaces a "Continue once approved"
  step. See the project notes for why this exception exists.

## Trade-offs

Firelynx is experimental. Driving a full Firefox per page costs time (seconds,
not instant) and memory, and aggressive extraction can occasionally drop useful
content. For everyday browsing, plain `lynx` is still faster and more reliable;
reach for Firelynx when a site genuinely needs JavaScript or refuses to serve a
text browser.
