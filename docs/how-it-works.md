# How Firelynx Works

Firelynx lets you browse the modern, JavaScript-heavy web from `lynx`, keeping
the fast, semantic, braille- and screen-reader-friendly text interface while a
real Firefox does the heavy lifting behind the scenes.

This document explains the architecture at a conceptual level. For day-to-day
usage see the [README](../README.md).

## The big picture

```
  lynx  ->  Firelynx proxy (Python, local)  ->  headless Firefox  ->  web
  (you) <-  clean semantic HTML             <-  rendered DOM      <-
```

1. `lynx` is configured to use the Firelynx proxy for both HTTP and HTTPS.
2. When you open a page, the proxy drives a headless Firefox (via Selenium
   WebDriver) to actually load and run the site, including its JavaScript.
3. The proxy extracts the meaningful content from the rendered page and returns
   clean, semantic HTML to `lynx`.
4. `lynx` renders that HTML the way it always has, so your keybindings,
   customizations, and braille workflow are unchanged.

The proxy and Firefox run entirely on your machine. Nothing about your browsing
is sent anywhere except to the sites you visit.

## Components

The Python code lives in `src/`:

- **`FirefoxProxy`** (`proxy_server.py`): owns the lifecycle. Starts the local
  HTTP server (stdlib `http.server`), launches the Firefox backend, picks an
  available port (so multiple sessions can run at once), and spawns `lynx` with
  the right proxy environment.
- **`HTTPProxyHandler`** (`proxy_handler.py`): handles each request from `lynx`
  (GET, POST, CONNECT) plus internal commands (filter changes, modal actions,
  page-control activation, MFA continue, the search form, form submission).
- **`FirefoxBackend`** (`firefox_backend.py`): manages the Selenium WebDriver,
  loads pages, waits for them to settle, and runs the extraction JavaScript.
- **`ContentProcessor`** (`content_processor.py`): turns extracted page data into
  the final HTML `lynx` sees, applies the content filter, renders page-action
  link lists, and makes links usable.
- **`FormProcessor`** (`form_processor.py`): form submission, multi-factor
  authentication detection, and converting JavaScript dialogs into accessible
  forms.

## Content extraction

A rendered modern page is mostly noise for a text browser. The guiding idea is
to behave like a screen reader rather than a scraper: the sites that matter most
are legally required to expose a correct accessibility contract (roles,
accessible names, landmarks, modal semantics), so Firelynx prefers those signals
and falls back to heuristics only when they are absent. Everything is generic,
with no per-site rules.

The layered pipeline (in `js/`), best result chosen by confidence:

1. **Assistive-hidden suppression.** Anything the page marks `aria-hidden="true"`
   or `inert` is excluded the way a screen reader excludes it, both during JS
   extraction and in a final Python pass. Guarded so a page never renders blank.
2. **Mozilla Readability.js.** The same algorithm as Firefox Reader Mode. Wins on
   articles.
3. **Landmark composition.** When a `main` landmark exists, the page is presented
   the way a screen reader announces it: `main` content first, each
   navigation landmark collapsed into a labelled link list below it (capped, with
   a note when links are omitted), and banner/footer/complementary regions left
   out. Wins on link-heavy and app-style pages where Readability gives up.
4. **Semantic scoring** of containers (tag, ARIA role, class/id hints, link
   density, text length) for pages with neither a usable article nor landmarks.
5. **Permissive fallback** that preserves more structure when the above filter
   too aggressively.

Structured data (JSON-LD, microdata) enhances the result with author, date, and
similar metadata. Link and button labels use the accessible name
(`aria-label`, `aria-labelledby`, text alternative) rather than raw text, so icon
controls still get usable words in `lynx`.

### Settle and re-extract

Modern pages change after they load: banners appear, dialogs open in response to
clicks, app views re-render. Instead of guessing with fixed delays, Firelynx
waits for the page to go quiet. `FirefoxBackend.wait_for_page_settle()` uses a
`MutationObserver` to detect when the DOM has stopped changing for a short window
(capped so static pages return fast), then re-runs the one unified extraction.
The same rule applies after every state change: initial load, a modal click, a
page-control activation, and form submission. A modal opening is not a special
event, it is simply the next page state.

### Interactive elements

- **Modal dialogs** are detected generically, in priority order: open native
  `<dialog>` elements, ARIA dialogs (`role="dialog"`, `aria-modal="true"`), the
  "background flip" pattern (the rest of the page marked `aria-hidden`/`inert`
  while one subtree stays exposed, which is how major sites signal modality), and
  finally a geometry fallback for ARIA-less overlays (a fixed, high z-index
  element covering the viewport with the body scroll-locked). A detected dialog
  is presented once, as an accessible HTML form. Clicking a button tells Firefox
  to activate the real element, then settle and re-extract.
- **Page actions.** Many app controls are `<button>` or `<div role="button">`
  with a JavaScript handler and no link, which `lynx` cannot activate. Firelynx
  captures these as structured data and renders them as a "Page actions" link
  list. Activating one clicks the real element in Firefox, settles, and
  re-extracts. How many controls are shown is governed by the content filter
  (see below).
- **Infinite-scroll feeds.** Virtualized feeds keep only a window of posts in the
  DOM. When Firelynx detects a feed (`role="feed"`, or several top-level
  `role="article"` elements), it scroll-harvests with de-duplication and renders
  the accumulated posts followed by a "Load more posts" link, so a feed becomes
  ordinary paginated reading.

### Content filter levels

The filter selector appears at the top of every page as
`Content: [minimal] [balanced] [all]`. Submitting one switches the view live
(it re-extracts the current page, no reload), and `--content` sets the startup
default. The levels trade completeness against clutter:

- **`minimal`**: reader mode. Readability.js only, just the article text. No page
  actions.
- **`balanced`** (default): the full pipeline above. Main content, collapsed
  navigation, detected dialogs, and the page actions found inside the `main`
  landmark.
- **`all`**: show nearly everything the page rendered, including every page
  action, not just those inside `main`.

Because the selector is right there on every page, it is the quick lever to pull
when a page shows too little (switch to `all`) or too much (switch to `minimal`).

## HTTPS without losing the path (ProxySSL)

A normal HTTP proxy learns the full URL of a request. But for HTTPS, `lynx`
issues an opaque `CONNECT host:443` and then speaks TLS, so the proxy never sees
the path. Firelynx solves this with **ProxySSL** (`proxyssl/`), a small
`LD_PRELOAD` shim loaded into `lynx`:

- For connections to the local Firelynx proxy, ProxySSL intercepts the OpenSSL
  calls and fakes the TLS handshake, passing the bytes through as plain HTTP.
- `lynx` therefore sends the complete `https://host/path` request in the clear,
  but only ever to `localhost`, never over the network.
- The proxy reads the full URL and hands it to Firefox, which makes the real TLS
  connection to the site.

ProxySSL only touches connections to the configured local proxy port
(`PROXYSSL_PORT`). All other TLS traffic uses the real OpenSSL functions
untouched. The library is built and wired in automatically by `install.sh`, and
the proxy sets `LD_PRELOAD` on the `lynx` subprocess for you.

On the ethics: this is TLS interception, which is normally a red flag. Here it is
strictly local and self-directed. You are intercepting your own traffic to your
own localhost proxy to make your own browser accessible. No third party is
involved and nothing leaves your machine unencrypted.

## Forms, dialogs, and MFA

- **Forms** are filled and submitted in the real Firefox, so JavaScript-driven
  forms work. Login-shaped submissions can take many seconds, so the proxy
  returns an immediate redirect and polls in the background so `lynx` does not
  time out. POST forms are routed through a plain-HTTP proxy endpoint to avoid an
  HTTPS-over-CONNECT round trip.
- **JavaScript dialogs** are converted to accessible forms as described above.
- **Multi-factor authentication** is detected from the live DOM (code fields,
  waiting states, common prompt text). Firelynx includes a small amount of
  Facebook-specific handling for its phone-approval push flow, the one place
  generic detection is not enough, which surfaces a "Continue once approved"
  step. See the project notes for why this exception exists.

## Trade-offs

Firelynx is experimental. Driving a full Firefox per page costs time (seconds,
not instant) and memory, and aggressive extraction can occasionally drop useful
content. For everyday browsing, plain `lynx` is still faster and more reliable.
Reach for Firelynx when a site genuinely needs JavaScript or refuses to serve a
text browser.
