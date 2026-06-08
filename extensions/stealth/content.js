/**
 * Firelynx Stealth Content Script (MV3, world: MAIN)
 *
 * Runs at document_start directly in the page's JavaScript context (not an
 * isolated sandbox), so modifications to navigator are immediately visible to
 * all page scripts — including bot detection that fires on initial page load.
 */

// Hide WebDriver flag
try {
    Object.defineProperty(navigator, 'webdriver', {
        get: function () { return undefined; },
        configurable: true
    });
} catch (e) {}

// Fix headless-mode window dimensions.
// In Firefox headless, outerWidth/outerHeight are 0 until set by the browser
// chrome — a strong fingerprinting signal that reliably identifies headless runs.
try {
    if (window.outerWidth === 0) {
        Object.defineProperty(window, 'outerWidth', {
            get: function () { return window.innerWidth || 1366; },
            configurable: true
        });
    }
    if (window.outerHeight === 0) {
        Object.defineProperty(window, 'outerHeight', {
            get: function () { return window.innerHeight || 768; },
            configurable: true
        });
    }
} catch (e) {}

// Ensure navigator.languages is populated (empty array is a headless signal)
try {
    if (!navigator.languages || navigator.languages.length === 0) {
        Object.defineProperty(navigator, 'languages', {
            get: function () { return ['en-US', 'en']; },
            configurable: true
        });
    }
} catch (e) {}
