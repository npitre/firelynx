/**
 * Infinite-scroll feed harvesting.
 *
 * Modern feeds (Facebook, X, Mastodon, Reddit) are VIRTUALIZED: only the posts
 * near the viewport are in the DOM; scrolling loads more and removes off-screen
 * ones. A single extraction therefore sees only a handful of posts. These
 * helpers let the proxy scroll incrementally and harvest posts as they pass
 * through the DOM, accumulating them on the Python side with de-duplication.
 *
 * Posts are identified generically as top-level [role="article"] — the standard
 * infinite-scroll signal — never per-site selectors.
 */

// djb2 hash → stable de-dup key for a post (its text doesn't change as the DOM
// recycles, so the same post harvested twice collapses to one)
function fxHash(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) {
        h = ((h << 5) + h) + s.charCodeAt(i);
        h |= 0;
    }
    return 'p' + (h >>> 0);
}

// A [role="article"] that is itself nested inside another article is a comment/
// embed, not a feed post — only harvest top-level ones.
function fxIsTopLevelArticle(a) {
    return !(a.parentElement && a.parentElement.closest('[role="article"]'));
}

/**
 * Is this an infinite-scroll feed? True when there's a [role="feed"] container
 * or several top-level articles.
 */
function detectFeed() {
    const hasFeedRole = !!document.querySelector('[role="feed"]');
    let topLevel = 0;
    document.querySelectorAll('[role="article"]').forEach(function (a) {
        if (fxIsTopLevelArticle(a)) topLevel++;
    });
    return { isFeed: hasFeedRole || topLevel >= 3, articleCount: topLevel, hasFeedRole: hasFeedRole };
}

/**
 * Currently-rendered feed posts: [{key, html, text}]. Skips assistive-hidden
 * and trivially short articles.
 */
function harvestVisibleArticles() {
    const out = [];
    document.querySelectorAll('[role="article"]').forEach(function (a) {
        if (!fxIsTopLevelArticle(a)) return;
        if (a.closest('[aria-hidden="true"], [inert]')) return;
        const text = (a.innerText || '').trim();
        if (text.length < 20) return;
        out.push({ key: fxHash(text.slice(0, 300)), html: a.innerHTML, text: text });
    });
    return out;
}

/**
 * Scroll down roughly one viewport (window, and any scrollable feed ancestor),
 * returning progress so the caller can detect "reached the bottom".
 */
function scrollFeedStep() {
    const step = Math.round((window.innerHeight || 768) * 0.85);
    const beforeY = window.scrollY;
    window.scrollBy(0, step);

    // Some feeds scroll an inner container rather than the window
    const feed = document.querySelector('[role="feed"]');
    if (feed) {
        let el = feed;
        while (el && el !== document.body) {
            const s = getComputedStyle(el);
            if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 10) {
                el.scrollTop += step;
                break;
            }
            el = el.parentElement;
        }
    }

    const maxY = (document.body.scrollHeight || 0) - (window.innerHeight || 0);
    return {
        y: window.scrollY,
        max: maxY,
        movedWindow: window.scrollY !== beforeY,
        atBottom: window.scrollY >= maxY - 2
    };
}
