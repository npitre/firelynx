/**
 * Modal Dialog Detection System — accessibility-first
 *
 * IMPORTANT: This JavaScript runs in Firefox and should NEVER reference the proxy server.
 * It only detects and catalogs modal elements for the proxy to handle.
 *
 * Architecture:
 * 1. Firefox detects modals and buttons
 * 2. Returns data about detected elements to Python
 * 3. Python/proxy generates lynx-friendly forms
 * 4. When forms submitted, Python tells Firefox to click original elements
 *
 * Detection uses the signals sites actually maintain, in priority order:
 *
 * 1. Top layer:       open native <dialog> elements
 * 2. ARIA:            visible [role="dialog"], [role="alertdialog"], [aria-modal="true"]
 * 3. Background flip: a body-level subtree marked aria-hidden/inert while a
 *                     sibling stays exposed — the exposed sibling hosts the
 *                     modal (how major sites signal modality to screen readers)
 * 4. Geometry:        only when 1–3 find nothing — the topmost fixed/sticky
 *                     element at sampled viewport points, accepted when the
 *                     page shows modality (scroll lock or full-page backdrop)
 *                     or the element covers a large viewport fraction.
 *                     Catches ARIA-less cookie banners without flagging
 *                     ordinary sticky headers.
 *
 * Click targets are re-resolved by accessible name when the element tagged at
 * detection time has been re-rendered away (framework-driven sites).
 */

const FIRELYNX_BUTTON_SELECTOR = 'button, input[type="submit"], input[type="button"], [role="button"]';

/**
 * Visibility check for CONTAINERS (dialogs, overlays). Works for
 * position:fixed elements — offsetParent is null for those, so it must not
 * be used here. Opacity counts: a faded-out dialog is not currently shown.
 */
function isElementVisible(element) {
    if (!element || !element.getClientRects || element.getClientRects().length === 0) {
        return false;
    }
    const style = getComputedStyle(element);
    return style.display !== 'none' &&
           style.visibility !== 'hidden' &&
           parseFloat(style.opacity || '1') > 0.05;
}

/**
 * True when the page hides this element from assistive technology:
 * aria-hidden="true" or inert on the element or any ancestor. Screen
 * readers skip such content entirely; every extraction layer should too.
 */
function isAssistiveHidden(element) {
    return !!(element && element.closest &&
              element.closest('[aria-hidden="true"], [inert]'));
}

/**
 * Operability check for CONTROLS (buttons, inputs). Deliberately ignores
 * opacity: a very common styling pattern (Amazon's UI toolkit among many)
 * hides the real control at near-zero opacity under a styled wrapper that
 * carries the visible label. The accessibility tree exposes such controls —
 * only display:none / visibility:hidden prune them — so we must too.
 */
function isElementOperable(element) {
    if (!element || !element.getClientRects || element.getClientRects().length === 0) {
        return false;
    }
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden';
}

/**
 * Lightweight accessible-name computation (subset of the ARIA spec, in
 * priority order). Gives icon buttons and span-stuffed buttons usable labels.
 */
function accessibleName(element) {
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

    const labelledBy = element.getAttribute('aria-labelledby');
    if (labelledBy) {
        const text = labelledBy.split(/\s+/).map(function (id) {
            const ref = document.getElementById(id);
            return ref ? (ref.innerText || ref.textContent || '') : '';
        }).join(' ').trim();
        if (text) return text;
    }

    if (element.tagName === 'INPUT' && element.value && element.value.trim()) {
        return element.value.trim();
    }

    const text = (element.innerText || element.textContent || '').trim();
    if (text) return text;

    const img = element.querySelector && element.querySelector('img[alt]');
    if (img && img.alt.trim()) return img.alt.trim();

    const title = element.getAttribute('title');
    if (title && title.trim()) return title.trim();

    return '';
}

/**
 * Explicit label only (aria-label / aria-labelledby) — no text fallback.
 * Used for naming dialogs, where innerText is the content, not the name.
 */
function explicitLabel(element) {
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
    const labelledBy = element.getAttribute('aria-labelledby');
    if (labelledBy) {
        const text = labelledBy.split(/\s+/).map(function (id) {
            const ref = document.getElementById(id);
            return ref ? (ref.innerText || ref.textContent || '') : '';
        }).join(' ').trim();
        if (text) return text;
    }
    return '';
}

/**
 * Label for a CONTROL: its accessible name, or — when the control itself is
 * nameless — the text of the nearest wrapper that contains exactly this one
 * control. Handles the styled-control pattern where the visible label is a
 * sibling span inside the button wrapper rather than part of the control.
 */
function buttonLabel(control) {
    const name = accessibleName(control);
    if (name) return name;

    let wrapper = control.parentElement;
    for (let depth = 0; wrapper && wrapper !== document.body && depth < 3; depth++) {
        const controls = wrapper.querySelectorAll(FIRELYNX_BUTTON_SELECTOR);
        if (controls.length > 1) {
            break; // wrapper spans multiple controls — its text is ambiguous
        }
        const text = (wrapper.innerText || '').trim();
        if (text) return text;
        wrapper = wrapper.parentElement;
    }
    return '';
}

/**
 * Find the containers that currently behave as modal dialogs.
 * Pure query — does not tag anything. Shared by detection and click
 * re-resolution so both see the same containers.
 */
function findModalContainers() {
    const found = [];

    // --- Signal 1: native <dialog> top layer ---
    document.querySelectorAll('dialog[open]').forEach(function (dlg) {
        if (isElementVisible(dlg) && !isAssistiveHidden(dlg)) found.push(dlg);
    });

    // --- Signal 2: ARIA dialog semantics ---
    document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]')
        .forEach(function (dlg) {
            if (isElementVisible(dlg) && !isAssistiveHidden(dlg)) found.push(dlg);
        });

    // --- Signal 3: background marked aria-hidden/inert ---
    // When a page hides its real content from assistive tech, whatever stays
    // exposed at the top level is the modal layer.
    const bodyChildren = Array.from(document.body ? document.body.children : []);
    const hiddenChildren = bodyChildren.filter(function (child) {
        return child.getAttribute('aria-hidden') === 'true' || child.hasAttribute('inert');
    });
    if (hiddenChildren.length > 0) {
        const hiddenTextLength = hiddenChildren.reduce(function (sum, child) {
            return sum + ((child.innerText || '').trim().length);
        }, 0);
        // Only meaningful when the hidden subtrees hold real page content
        // (not decorative aria-hidden snippets like icon sprites).
        if (hiddenTextLength > 200) {
            bodyChildren.forEach(function (child) {
                if (hiddenChildren.indexOf(child) === -1 &&
                    !/^(SCRIPT|STYLE|LINK|TEMPLATE)$/.test(child.tagName) &&
                    isElementVisible(child)) {
                    found.push(child);
                }
            });
        }
    }

    // --- Signal 4: geometry (only when semantics found nothing) ---
    if (found.length === 0) {
        findOverlaysByGeometry().forEach(function (el) { found.push(el); });
    }

    // Dedupe, keeping the innermost of nested candidates: when a portal
    // wrapper or backdrop contains the actual dialog, the inner element is
    // the precise scope for buttons.
    const unique = Array.from(new Set(found));
    return unique.filter(function (el) {
        return !unique.some(function (other) {
            return other !== el && el.contains(other);
        });
    });
}

/**
 * Geometry-based overlay detection for ARIA-less modals (cookie banners).
 *
 * A modal, by definition, visually covers the page — so instead of scanning
 * every element's computed style, sample a 3x3 grid of viewport points and
 * take the topmost fixed/sticky element of each hit stack. A sampled element
 * qualifies when the page shows modality (scroll lock on body/html, or a
 * full-viewport backdrop among the samples) or when it covers a large
 * fraction of the viewport itself. Ordinary sticky headers fail all three
 * conditions and stay excluded.
 */
function findOverlaysByGeometry() {
    const vw = window.innerWidth, vh = window.innerHeight;
    if (!vw || !vh || !document.elementsFromPoint) return [];

    const bodyStyle = document.body ? getComputedStyle(document.body) : null;
    const htmlStyle = getComputedStyle(document.documentElement);
    const scrollLocked = /hidden|clip/.test(
        (bodyStyle ? bodyStyle.overflow + bodyStyle.overflowY : '') +
        htmlStyle.overflow + htmlStyle.overflowY);

    const sampled = new Set();
    [0.1, 0.5, 0.9].forEach(function (fx) {
        [0.1, 0.5, 0.9].forEach(function (fy) {
            const stack = document.elementsFromPoint(vw * fx, vh * fy);
            for (let i = 0; i < stack.length; i++) {
                const position = getComputedStyle(stack[i]).position;
                if (position === 'fixed' || position === 'sticky') {
                    sampled.add(stack[i]);
                    break; // only the topmost overlay at this point
                }
            }
        });
    });

    const coverageOf = function (el) {
        const rect = el.getBoundingClientRect();
        return (rect.width * rect.height) / (vw * vh);
    };
    const backdropSeen = Array.from(sampled).some(function (el) {
        return coverageOf(el) >= 0.8;
    });

    return Array.from(sampled).filter(function (el) {
        return isElementVisible(el) &&
               (coverageOf(el) >= 0.25 || scrollLocked || backdropSeen);
    });
}

/**
 * Map button text to a coarse semantic action (used for logging and the
 * form-field encoding; clicking re-resolves by accessible name, not by this).
 */
function semanticAction(label) {
    const words = ' ' + label.toLowerCase().replace(/[^a-z0-9]+/g, ' ') + ' ';
    if (/ (ok|okay|accept|agree|allow|yes) /.test(words)) return 'accept';
    if (/ (cancel|close|dismiss|reject|deny|no) /.test(words)) return 'cancel';
    if (/ (continue|proceed|next|submit) /.test(words)) return 'continue';
    return 'click_button';
}

/**
 * Detect modal dialogs and their buttons.
 * Returns data about found elements WITHOUT generating proxy-referencing forms.
 */
function detectModalElements() {
    const detectedElements = {
        modals: [],
        buttons: [],
        totalElements: 0
    };

    // Clear markers from previous extractions so IDs stay deterministic and
    // never point at stale elements.
    document.querySelectorAll('[data-modal-id]').forEach(function (el) {
        el.removeAttribute('data-modal-id');
    });

    const containers = findModalContainers();

    containers.forEach(function (dialog, dialogIndex) {
        const visibleButtons = Array.from(
            dialog.querySelectorAll(FIRELYNX_BUTTON_SELECTOR)
        ).filter(isElementOperable);

        if (visibleButtons.length === 0) {
            return; // nothing actionable (e.g. a bare backdrop)
        }

        const dialogId = 'modal-dialog-' + dialogIndex;
        dialog.setAttribute('data-modal-id', dialogId);

        detectedElements.modals.push({
            elementId: dialogId,
            name: explicitLabel(dialog).slice(0, 80),
            // The dialog's prose, shown alongside its buttons in the proxy's
            // converted interface — the ONE place dialog content is rendered
            text: (dialog.innerText || '').trim().slice(0, 600),
            buttonCount: visibleButtons.length,
            visible: true
        });

        visibleButtons.forEach(function (button, buttonIndex) {
            const label = buttonLabel(button);
            if (!label || label.length > 80) {
                return;
            }
            const elementId = 'modal-btn-' + dialogIndex + '-' + buttonIndex;
            button.setAttribute('data-modal-id', elementId);

            detectedElements.buttons.push({
                elementId: elementId,
                text: label,
                action: semanticAction(label),
                tagName: button.tagName.toLowerCase(),
                isVisible: true,
                dialogId: dialogId
            });
        });
    });

    detectedElements.totalElements =
        detectedElements.buttons.length + detectedElements.modals.length;
    return detectedElements;
}

/**
 * Click an element previously detected by detectModalElements().
 * Called by the Python proxy when the user submits a converted modal form.
 *
 * Strategy 1: the element tagged at detection time, if it still exists.
 * Strategy 2: re-resolve by accessible name — first inside the containers
 *             that are modal RIGHT NOW, then anywhere on the page. This is
 *             what makes clicks survive framework re-renders: the user chose
 *             a label, not a DOM node.
 *
 * Never blind-clicks an unrelated element; failure is reported to the proxy.
 */
function clickModalElement(elementId, actionType, label) {
    try {
        // Strategy 1: stored element ID
        if (elementId) {
            const tagged = document.querySelector('[data-modal-id="' + elementId + '"]');
            if (tagged && isElementOperable(tagged)) {
                tagged.click();
                return { success: true, method: 'stored_id', element: tagged.tagName };
            }
        }

        // Strategy 2: re-resolve by accessible name
        const wanted = (label || '').trim().toLowerCase();
        if (wanted) {
            const scopes = findModalContainers();
            scopes.push(document);
            for (let s = 0; s < scopes.length; s++) {
                const buttons = scopes[s].querySelectorAll(FIRELYNX_BUTTON_SELECTOR);
                for (let b = 0; b < buttons.length; b++) {
                    if (!isElementOperable(buttons[b])) continue;
                    if (buttonLabel(buttons[b]).trim().toLowerCase() === wanted) {
                        buttons[b].click();
                        return { success: true, method: 'accessible_name', element: buttons[b].tagName };
                    }
                }
            }
        }

        return {
            success: false,
            reason: 'No visible element matching "' + (label || actionType || elementId) + '"'
        };

    } catch (e) {
        return { success: false, reason: e.message };
    }
}

/**
 * Tag in-content JS-driven controls so the proxy can make them activatable
 * in lynx. Facebook (and React apps generally) build actionable controls as
 * <div role="button"> / <button> with JavaScript click handlers and no href —
 * lynx cannot activate them. We tag each live control with data-fx-click="<id>"
 * (so it survives into the extracted innerHTML snapshot) and data-fx-main="1"
 * when it sits inside the main content landmark. The Python side decides which
 * to convert into activatable links based on the content-filter level.
 *
 * Excluded: assistive-hidden controls, invisible controls, controls inside a
 * <form> (handled as submit inputs) or inside a detected modal dialog (handled
 * by the converted dialog interface), and controls with no accessible name.
 *
 * Returns a structured list [{id, name, main}] captured in this synchronous
 * pass — reliable even on apps that re-render and wipe attributes moments
 * later (the feed does this). The proxy renders the list; clicking re-resolves
 * by id (fast path) or accessible name (after re-render). Each control is also
 * tagged data-fx-click="<id>" for the fast click path.
 *
 * Excluded: assistive-hidden / invisible controls, controls inside a <form>
 * (handled as submit inputs) or a detected modal dialog (handled by the
 * converted dialog box), and controls with no accessible name.
 *
 * Idempotent: clears prior tags first so ids stay deterministic per extraction.
 */
function tagActivatablePageControls() {
    document.querySelectorAll('[data-fx-click]').forEach(function (el) {
        el.removeAttribute('data-fx-click');
    });

    const modalContainers = findModalContainers();
    const inModal = function (el) {
        return modalContainers.some(function (c) { return c === el || c.contains(el); });
    };
    const mainEl = document.querySelector('main, [role="main"], article');

    const controls = [];
    let n = 0;
    document.querySelectorAll('[role="button"], button').forEach(function (el) {
        if (isAssistiveHidden(el) || !isElementOperable(el)) return;
        if (el.closest('form')) return;     // submit handled as <input type=submit>
        if (inModal(el)) return;            // handled by the converted dialog box
        const name = buttonLabel(el);
        if (!name || name.length > 80) return;
        const id = 'fx-' + n++;
        el.setAttribute('data-fx-click', id);
        controls.push({ id: id, name: name, main: !!(mainEl && mainEl.contains(el)) });
    });
    return controls;
}

/**
 * Click a page control previously tagged by tagActivatablePageControls().
 * Tag first (fast path); if the framework re-rendered it away, re-resolve by
 * accessible name among current controls — same resilience as modal clicks.
 */
function clickPageControl(controlId, name) {
    try {
        if (controlId) {
            const tagged = document.querySelector('[data-fx-click="' + controlId + '"]');
            if (tagged && isElementOperable(tagged)) {
                tagged.click();
                return { success: true, method: 'tag', element: tagged.tagName };
            }
        }
        const wanted = (name || '').trim().toLowerCase();
        if (wanted) {
            const els = document.querySelectorAll('[role="button"], button');
            for (let i = 0; i < els.length; i++) {
                if (!isElementOperable(els[i])) continue;
                if (buttonLabel(els[i]).trim().toLowerCase() === wanted) {
                    els[i].click();
                    return { success: true, method: 'accessible_name', element: els[i].tagName };
                }
            }
        }
        return { success: false, reason: 'No control matching "' + (name || controlId) + '"' };
    } catch (e) {
        return { success: false, reason: e.message };
    }
}
