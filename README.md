# Firelynx: Where Firefox Meets lynx

⚠️ **EXPERIMENTAL ALPHA SOFTWARE** - Firelynx is in early development. 
While functional for many websites, expect bugs, limitations, and breaking 
changes. Use at your own risk and keep backups of important work.

The web has moved on, but accessible browsing hasn't kept up.
Firelynx changes that by creating a bridge between the excellent
accessibility of lynx and the modern web compatibility of Firefox.

## The Problem: When the Web Left Accessibility Behind

If you're a blind user, you know the frustration. Traditional text browsers
like lynx work beautifully with braille displays and screen readers - they're
fast, efficient, and give you exactly the semantic information you need.
But try to use Google Search, Facebook, or most modern websites, and you're
out of luck. These sites require JavaScript to function, leaving lynx users
stuck with broken layouts, completely blank pages, or worse - some sites
simply refuse to serve you if your browser isn't one of the big ones.

On the other hand, Firefox is a powerhouse that handles any modern website
perfectly. But running Firefox in a GUI environment when you're using a
braille display is like using a sledgehammer to crack a nut. You're forced
to navigate through complex visual interfaces, deal with mouse-centric
designs, and lose the clean, efficient text-based interaction that makes
computing accessible in the first place.

### What About term.everything?

You might have heard of [term.everything](https://github.com/mmulet/term.everything) - 
a project that promises to render GUI applications in the terminal as ASCII 
art. Sounds perfect, right? Unfortunately, it doesn't solve our problem at 
all. 

When term.everything displays Firefox, you get something like this:
```
🬽 ▀▀▀▀▀▀🬼   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
˛🬠▂▂▂   ▌ ┃    🬼                                            ▎
```

This is just visual noise to a braille display. There's no semantic 
structure, no way to understand what's a heading versus a link versus body 
text. It's graphics made of text characters - completely inaccessible to 
the tools that blind users rely on.

## The Solution: Firelynx

The breakthrough came from realizing we didn't need to choose between lynx 
and Firefox. What if we could use Firefox as a backend engine to process 
modern websites, then serve the results through lynx's familiar, 
accessible interface?

That's exactly what Firelynx does. It creates an HTTP proxy server that 
sits between lynx and the web. When you browse with lynx, the proxy 
intercepts your requests, processes them through a headless Firefox 
instance (complete with JavaScript execution), extracts the meaningful 
content, and serves it back to lynx as clean, semantic HTML.

The result? You get to keep lynx's excellent accessibility - all your
customizations, keyboard shortcuts, and the clean interface that works
perfectly with your braille display in a text-based terminal with
[BRLTTY](http://brltty.app/) - while gaining access to any modern website
that Firefox can handle.

## Features That Make It Work

**Modern Web Compatibility**: Google Search, Facebook, complex JavaScript 
sites - they all work because Firefox handles the heavy lifting.

**Seamless lynx Experience**: Every lynx feature works exactly as before. 
Your muscle memory, customizations, and workflow remain unchanged.

**Smart Content Extraction**: The system intelligently finds and
prioritizes main content while filtering out navigation clutter and ads.

**Runtime Content Filtering**: Choose your preferred balance between showing
everything (bridge mode) and clean reading (filtered mode) with instant
switching between filter levels.

## How It Works

In short: lynx talks to a small local proxy, the proxy drives a headless Firefox
to render and run each page, and the extracted semantic content comes back to
lynx as clean HTML. For the full architecture — content extraction, HTTPS via
ProxySSL, forms, and MFA — see [docs/how-it-works.md](docs/how-it-works.md).

## Installation

**Requirements:** a Linux system with Python **3.8 or newer** (3.9+ recommended
— Selenium 4.x sets the floor), Firefox, and lynx. `./install.sh` also builds
the bundled ProxySSL library (needed for HTTPS), so a C compiler and the OpenSSL
headers are required.

### Fedora Linux
```bash
# Install the packages (gcc/make/openssl-devel are needed to build ProxySSL)
sudo dnf install python3-selenium selenium-manager firefox lynx gcc make openssl-devel

# Get and install Firelynx
git clone https://github.com/npitre/firelynx.git
cd firelynx/
./install.sh

# Now you can use 'firelynx' from anywhere
firelynx https://example.com
```

### Debian/Ubuntu

Heads-up: Debian's packaged `python3-selenium` can't fetch geckodriver on its
own — its bundled Selenium Manager is broken and geckodriver isn't in the Debian
repos. The reliable path is a virtualenv with pip's Selenium, which ships a
working driver manager. Run the steps below (and `firelynx` itself) with that
venv active, so `python3` is the one that has Selenium.

```bash
# System packages (gcc/make/libssl-dev build ProxySSL; firefox-esr on Debian)
sudo apt update
sudo apt install firefox-esr lynx gcc make libssl-dev python3-venv git

# Selenium from pip in a venv — its bundled manager downloads geckodriver for you
python3 -m venv ~/.venvs/firelynx
source ~/.venvs/firelynx/bin/activate
pip install selenium

# Get and install Firelynx (venv still active)
git clone https://github.com/npitre/firelynx.git
cd firelynx/
./install.sh

# Use it (keep the venv active in the shell you run it from)
firelynx https://example.com
```

Don't install `webdriver-manager` — Firelynx doesn't use it. If you prefer the
system `python3-selenium`, you'll need to install geckodriver yourself from
<https://github.com/mozilla/geckodriver/releases> and place it on your `PATH`.

## How to Use

**Start browsing any website:**
```bash
firelynx https://facebook.com
firelynx google.com  
firelynx
```

This launches lynx connected to the Firefox proxy. Navigate exactly as you 
normally would in lynx - press 'g' to go to URLs, follow links normally, 
fill out forms as usual. You now have a much greater chance for 
JavaScript-heavy sites to work to some extent.

**For quick text output without interactive lynx:**
```bash
firelynx --dump --search "python tutorial"
firelynx --dump https://example.com
firelynx --dump --search "weather forecast" --engine bing
```

## Current Limitations

**JavaScript Modal Dialogs**: Some modal dialogs (like Facebook's device 
trust prompts) don't convert properly yet. The detection system is there 
but needs refinement.

**Google Search CAPTCHAs**: Google still detects the automation and shows 
robot verification. Use DuckDuckGo instead - it works perfectly and doesn't 
have this issue.

**MFA Timing**: On sites like Facebook, if you press the "continue" button 
too quickly during multi-factor authentication, you might not get the 
expected retry prompts.

**Some False Positives**: Occasionally sites like Amazon trigger MFA 
warnings when no authentication is actually required.

Despite these quirks, the browser handles many websites well, including
complex authentication flows, form submissions, and JavaScript-heavy content
that traditional text browsers simply can't access.

## Credits

Content extraction uses Mozilla's [Readability.js](https://github.com/mozilla/readability)
(`js/readability.js`), the Firefox Reader Mode library, vendored under the
Apache License 2.0.

## License

Firelynx is licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE)
and [NOTICE](NOTICE). Copyright 2026 Nicolas Pitre.
