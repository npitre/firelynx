// === HYBRID CONTENT EXTRACTION SYSTEM ===
/**
 * Multi-strategy content extraction with confidence scoring
 * Combines proven algorithms to maximize extraction success
 */
function extractContentHybrid() {
    const strategies = [];

    // Modal containers are presented separately by the proxy's converted
    // dialog interface (name + text + buttons). Content strategies must
    // exclude them so dialog prose is never duplicated into page content.
    const modalContainers = findModalContainers();
    const inModalContainer = function (el) {
        return modalContainers.some(function (c) { return c === el || c.contains(el); });
    };

    // Strategy 1: Mozilla Readability.js (highest confidence)
    // Why: Proven in production, handles complex layouts, link density analysis
    try {
        const documentClone = document.cloneNode(true);
        // Remove dialog markup from the clone — its content lives in the
        // converted dialog interface, not the article
        documentClone.querySelectorAll(
            'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]'
        ).forEach(function (el) { el.remove(); });
        const reader = new Readability(documentClone, {
            // Configure for accessibility - prefer semantic elements
            debug: false,
            maxElemsToParse: 0,  // No limit for thorough analysis
            nbTopCandidates: 5   // Analyze multiple candidates
        });
        const article = reader.parse();
        
        if (article && article.textContent && article.textContent.length > 100) {
            strategies.push({
                method: 'readability',
                confidence: 0.95,
                title: article.title || document.title,
                content: article.textContent,
                htmlContent: article.content,
                length: article.length,
                excerpt: article.excerpt,
                source: 'Mozilla Readability.js'
            });
        }
    } catch (e) {
        console.warn('Readability.js extraction failed:', e.message);
    }
    
    // Strategy 2: Landmark composition (medium-high confidence)
    // Why: Landmarks are the page's own structure declaration for screen
    // readers — main first, navigation collapsed, chrome omitted
    const landmarkResult = extractViaLandmarks(inModalContainer);
    if (landmarkResult) {
        strategies.push(landmarkResult);
    }

    // Strategy 3: Enhanced Semantic Scoring (lower confidence)
    // Why: Fallback for sites without proper semantic markup
    const semanticResult = extractViaSemanticScoring(inModalContainer);
    if (semanticResult) {
        strategies.push(semanticResult);
    }

    // Strategy 4: Structured Data Enhancement
    // Why: JSON-LD and microdata provide explicit content metadata
    const structuredData = extractStructuredData();
    
    // Select best strategy and enhance with structured data
    const bestResult = selectBestStrategy(strategies);
    if (bestResult && structuredData) {
        enhanceWithStructuredData(bestResult, structuredData);
    }
    
    // CRITICAL: Check if extraction was successful enough
    // If advanced methods produced very little content, use fallback extraction
    if (bestResult && shouldUseFallbackExtraction(bestResult)) {
        // Add debug info to the result to indicate fallback was needed
        bestResult.needsFallback = true;
        bestResult.fallbackReason = 'Insufficient content from advanced extraction';
        return bestResult; // Python code can check needsFallback flag
    }
    
    return bestResult || createFallbackResult();
}

/**
 * Determine if fallback extraction should be used
 * Criteria: Advanced extraction produced insufficient or overly filtered content
 */
function shouldUseFallbackExtraction(result) {
    if (!result) return true;
    
    // Check content length - if too short, fallback likely needed
    const contentLength = result.content?.length || 0;
    if (contentLength < 200) {
        return true; // Very short content suggests over-aggressive filtering
    }
    
    // Check link count - content-rich pages should expose a reasonable number of links
    const extraction = result.extraction || {};
    const linkCount = extraction.linkCount || 0;
    if (linkCount < 5) {
        return true; // Too few links suggests missing navigation/content
    }
    
    // Check if extraction method suggests problems
    const method = extraction.method;
    if (method === 'fallback' || method === 'fallback_failed') {
        return true; // Already failed, definitely need better fallback
    }
    
    // URL-keyword heuristic for link-heavy sites (shopping / marketplace pages):
    // these are navigation-dense, so a low link count signals over-filtering.
    const url = window.location.href.toLowerCase();
    const isLinkHeavySite = url.includes('amazon') || url.includes('shop') ||
                       url.includes('store') || url.includes('buy');

    if (isLinkHeavySite && linkCount < 15) {
        return true; // Link-heavy sites should expose lots of navigation
    }
    
    // Check if this is primarily a form/login page - Readability.js removes forms
    const formCount = document.querySelectorAll('form').length;
    const inputCount = document.querySelectorAll('input').length;
    const textContent = document.body.innerText || '';
    
    if (formCount > 0 && textContent.length < 1000) {
        // Page has forms but little text content - likely a form-centric page
        return true; // Use fallback to preserve form elements
    }
    
    if (inputCount >= 2 && textContent.toLowerCase().includes('password')) {
        // Multiple inputs including password field - likely login form
        return true; // Use fallback to preserve login forms
    }
    
    return false; // Advanced extraction seems sufficient
}

/**
 * Escape text for safe embedding in generated HTML
 */
function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Landmark name from explicit labelling only (aria-label/aria-labelledby).
 * Deliberately NOT the accessible-name text fallback: a landmark's innerText
 * is its entire content, not its name.
 */
function landmarkName(element) {
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
 * Compose the page the way a screen reader presents it: the main landmark
 * first, navigation landmarks collapsed to labelled link lists below it,
 * banner/contentinfo/complementary omitted entirely (a braille user gets the
 * page's purpose first, chrome never).
 *
 * Rationale: landmarks are the structure the page itself maintains for
 * assistive technology — the signal redesigns are not allowed to break.
 */
function extractViaLandmarks(inModalContainer) {
    const main = document.querySelector('main, [role="main"]');
    if (!main || isAssistiveHidden(main) || (inModalContainer && inModalContainer(main))) return null;

    const mainText = main.innerText?.trim() || '';
    if (mainText.length < 50) return null;

    const htmlParts = ['<div class="landmark-main">' + main.innerHTML + '</div>'];
    const textParts = [mainText];
    let navCount = 0;
    const NAV_LINK_LIMIT = 50; // mega-menus: collapsed, not dumped wholesale

    document.querySelectorAll('nav, [role="navigation"]').forEach(function (nav) {
        if (isAssistiveHidden(nav) || main.contains(nav) || nav.contains(main)) return;
        const links = Array.from(nav.querySelectorAll('a[href]'))
            .map(function (a) { return { name: accessibleName(a), href: a.getAttribute('href') }; })
            .filter(function (l) { return l.name && l.href; });
        if (links.length === 0) return;

        navCount++;
        const heading = landmarkName(nav) || 'Navigation';
        const shown = links.slice(0, NAV_LINK_LIMIT);
        const items = shown.map(function (l) {
            return '<li><a href="' + escapeHtml(l.href) + '">' + escapeHtml(l.name) + '</a></li>';
        }).join('');
        const more = links.length > NAV_LINK_LIMIT
            ? '<p><em>(' + (links.length - NAV_LINK_LIMIT) + ' more links not shown)</em></p>'
            : '';
        htmlParts.push('<div class="landmark-nav"><h2>' + escapeHtml(heading) + '</h2><ul>' + items + '</ul>' + more + '</div>');
        textParts.push(heading + ':\n' + shown.map(function (l) { return l.name; }).join('\n'));
    });

    return {
        method: 'landmarks',
        confidence: 0.88,
        title: document.title || 'No Title',
        content: textParts.join('\n\n'),
        htmlContent: htmlParts.join('<hr class="landmark-separator">'),
        navLandmarks: navCount,
        source: 'Landmark composition (main first, navigation collapsed)'
    };
}

/**
 * Enhanced semantic scoring system - Multi-Section Extraction
 * Rationale: Modern websites have multiple important content areas, not just one "main" area
 */
function extractViaSemanticScoring(inModalContainer) {
    // Find all potential content containers
    const candidates = document.querySelectorAll('div, section, article, main, aside');
    const scoredElements = [];

    for (const element of candidates) {
        if (isAssistiveHidden(element)) continue;
        if (inModalContainer && inModalContainer(element)) continue;
        const score = scoreElement(element);
        const textLength = element.innerText?.trim().length || 0;
        
        if (score > 0 && textLength > 50) {  // Lower threshold to catch more content
            scoredElements.push({
                element,
                score,
                textLength,
                selector: generateSelector(element)
            });
        }
    }
    
    // Sort by composite score (content quality + text length)
    scoredElements.sort((a, b) => {
        const aComposite = a.score + Math.log(a.textLength);
        const bComposite = b.score + Math.log(b.textLength);
        return bComposite - aComposite;
    });
    
    if (scoredElements.length > 0) {
        // MULTI-SECTION APPROACH: Combine multiple significant content areas
        const significantElements = [];
        const combinedContent = [];
        const combinedHtml = [];
        
        // Take top scoring elements, but avoid nested duplicates
        for (const candidate of scoredElements) {
            const isNested = significantElements.some(existing => 
                existing.element.contains(candidate.element) || 
                candidate.element.contains(existing.element)
            );
            
            if (!isNested && significantElements.length < 5) {
                significantElements.push(candidate);
                
                // Add section with separator
                const sectionText = candidate.element.innerText?.trim() || '';
                if (sectionText) {
                    combinedContent.push(sectionText);
                    combinedHtml.push(`<div class="content-section">${candidate.element.innerHTML}</div>`);
                }
            }
        }
        
        return {
            method: 'semantic_scoring',
            confidence: Math.min(0.75, significantElements[0]?.score / 100 || 0),
            title: document.title || 'No Title',
            content: combinedContent.join('\n\n' + '─'.repeat(80) + '\n\n'),
            htmlContent: combinedHtml.join('<hr class="section-separator">'),
            score: significantElements[0]?.score || 0,
            selector: significantElements.map(e => e.selector).join(', '),
            sections: significantElements.length,
            source: 'Multi-section semantic extraction'
        };
    }
    
    return null;
}

/**
 * Score DOM elements based on content indicators
 * Rationale: Quantified heuristics are more reliable than hardcoded rules
 */
function scoreElement(element) {
    let score = 0;
    
    // Semantic HTML5 element bonus
    // Why: Semantic elements indicate content structure intent
    const tagBonuses = {
        'main': 100, 'article': 90, 'section': 70,
        'div': 20, 'p': 10, 'span': 5
    };
    score += tagBonuses[element.tagName.toLowerCase()] || 0;
    
    // ARIA role bonus
    // Why: ARIA roles explicitly declare element purpose
    const role = element.getAttribute('role');
    const roleBonuses = {
        'main': 100, 'article': 90, 'region': 70,
        'navigation': -60, 'banner': -50, 'contentinfo': -50,
        'complementary': -40, 'search': -30
    };
    score += roleBonuses[role] || 0;
    
    // Class and ID content indicators
    // Why: Common naming conventions indicate content areas
    const className = (element.className || '').toLowerCase();
    const id = (element.id || '').toLowerCase();
    
    const contentIndicators = ['content', 'main', 'article', 'post', 'body', 'text', 'story'];
    const noiseIndicators = ['nav', 'menu', 'sidebar', 'header', 'footer', 'ads', 'comments'];
    
    contentIndicators.forEach(indicator => {
        if (className.includes(indicator) || id.includes(indicator)) {
            score += 25;
        }
    });
    
    noiseIndicators.forEach(indicator => {
        if (className.includes(indicator) || id.includes(indicator)) {
            score -= 35;
        }
    });
    
    // Link density penalty
    // Why: High link density indicates navigation, not content
    const linkDensity = calculateLinkDensity(element);
    if (linkDensity > 0.5) score -= 50;
    if (linkDensity > 0.8) score -= 100;
    
    // Text length bonus
    // Why: Substantial text suggests main content area
    const textLength = element.innerText?.trim().length || 0;
    if (textLength > 500) score += 30;
    if (textLength > 1500) score += 50;
    
    // Nesting penalty for deeply nested elements
    // Why: Content is usually not buried too deep
    const depth = getElementDepth(element);
    if (depth > 10) score -= (depth - 10) * 5;
    
    return score;
}

/**
 * Calculate link density (ratio of link text to total text)
 * High link density indicates navigation rather than content
 */
function calculateLinkDensity(element) {
    const totalText = element.innerText?.trim().length || 0;
    if (totalText === 0) return 1; // No text = 100% noise
    
    const links = element.querySelectorAll('a');
    const linkText = Array.from(links).reduce((sum, link) => {
        return sum + (link.innerText?.trim().length || 0);
    }, 0);
    
    return linkText / totalText;
}

/**
 * Calculate DOM tree depth for an element
 * Deep nesting often indicates non-content elements
 */
function getElementDepth(element) {
    let depth = 0;
    let parent = element.parentElement;
    while (parent) {
        depth++;
        parent = parent.parentElement;
    }
    return depth;
}

/**
 * Generate a readable CSS selector for debugging
 */
function generateSelector(element) {
    const tagName = element.tagName.toLowerCase();
    const id = element.id ? `#${element.id}` : '';
    const className = element.className ? `.${element.className.split(' ').filter(c => c).join('.')}` : '';
    return `${tagName}${id}${className}`;
}

/**
 * Extract structured data (JSON-LD, microdata) for content enhancement
 * Why: Explicit semantic markup provides authoritative content metadata
 */
function extractStructuredData() {
    const structuredData = { jsonLd: [], microdata: [] };
    
    // Extract JSON-LD
    const jsonLdScripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of jsonLdScripts) {
        try {
            const data = JSON.parse(script.textContent);
            structuredData.jsonLd.push(data);
        } catch (e) {
            // Invalid JSON-LD, ignore
        }
    }
    
    // Extract microdata items
    const microdataItems = document.querySelectorAll('[itemscope]');
    for (const item of microdataItems) {
        const itemData = {
            type: item.getAttribute('itemtype'),
            properties: {}
        };
        
        const props = item.querySelectorAll('[itemprop]');
        for (const prop of props) {
            const name = prop.getAttribute('itemprop');
            const value = prop.getAttribute('content') || 
                         prop.getAttribute('datetime') ||
                         prop.textContent?.trim() || '';
            if (name && value) {
                itemData.properties[name] = value;
            }
        }
        
        structuredData.microdata.push(itemData);
    }
    
    return structuredData.jsonLd.length > 0 || structuredData.microdata.length > 0 ? 
           structuredData : null;
}

/**
 * Enhance extraction result with structured data
 * Why: Structured data provides authoritative metadata (author, date, etc.)
 */
function enhanceWithStructuredData(result, structuredData) {
    if (!result || !structuredData) return;
    
    result.structuredData = structuredData;
    
    // Enhance with JSON-LD data
    for (const data of structuredData.jsonLd) {
        if (data['@type'] === 'Article' || data['@type'] === 'NewsArticle' || data['@type'] === 'BlogPosting') {
            result.enhancedTitle = data.headline || result.title;
            result.author = data.author?.name || data.author;
            result.datePublished = data.datePublished;
            result.description = data.description;
            break;
        }
    }
    
    // Enhance with microdata
    for (const item of structuredData.microdata) {
        if (item.type?.includes('Article')) {
            result.enhancedTitle = item.properties.headline || result.title;
            result.author = item.properties.author;
            result.datePublished = item.properties.datePublished;
            break;
        }
    }
}

/**
 * Return the highest-confidence strategy, annotated with everything tried.
 * (Modal/dialog content is no longer a content strategy: detected dialogs
 * are rendered once, by the proxy's converted dialog interface.)
 */
function selectBestStrategy(strategies) {
    if (strategies.length === 0) return null;

    strategies.sort((a, b) => b.confidence - a.confidence);

    const best = strategies[0];
    best.extractionMethods = strategies.map(s => ({
        method: s.method,
        confidence: s.confidence,
        source: s.source
    }));

    return best;
}

/**
 * Create fallback result when all strategies fail
 * Why: Always return something usable rather than failing completely
 */
function createFallbackResult() {
    const body = document.querySelector('body');
    return {
        method: 'fallback',
        confidence: 0.1,
        title: document.title || 'No Title',
        content: body?.innerText?.trim() || 'No content found',
        htmlContent: body?.innerHTML || '',
        source: 'Document body fallback',
        extractionMethods: [{ method: 'fallback', confidence: 0.1, source: 'Last resort' }]
    };
}

/**
 * Main execution function that coordinates the entire extraction process
 * Returns comprehensive result with content, links, and metadata
 */
function executeContentExtraction() {
    // Capture JS-driven controls (role=button) as structured data in this
    // synchronous pass — reliable even on apps that re-render moments later.
    // The proxy renders them as activatable links; see modal-detection.js.
    let pageControls = [];
    try {
        if (typeof tagActivatablePageControls === 'function') {
            pageControls = tagActivatablePageControls();
        }
    } catch (e) { /* best-effort */ }

    // === EXECUTE EXTRACTION ===
    const extractionResult = extractContentHybrid();
    
    // === EXTRACT LINKS FROM CONTENT AREA ===
    /**
     * Extract links from the identified content area
     * Why: Links in main content are more relevant than navigation links
     */
    let contentElement = null;
    if ((extractionResult.method === 'readability' || extractionResult.method === 'landmarks') &&
        extractionResult.htmlContent) {
        // For composed/cleaned HTML results, parse that HTML so the link list
        // matches what the user actually sees (including collapsed navigation)
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = extractionResult.htmlContent;
        contentElement = tempDiv;
    } else {
        // For other methods, try to find the source element
        contentElement = document.querySelector('main, article, [role="main"]') || document.body;
    }

    // Accessible names give icon links usable labels; assistive-hidden links
    // are skipped just as a screen reader would skip them.
    const links = Array.from((contentElement || document).querySelectorAll('a[href]'))
        .filter(link => !isAssistiveHidden(link))
        .map(link => ({
            text: accessibleName(link).trim(),  // from modal-detection.js, always bundled first
            url: link.href
        }))
        .filter(link => link.text && link.text.length > 0 && link.url && !link.url.startsWith('javascript:'));
    
    // === DETECT MODAL ELEMENTS ===
    // Note: detectModalElements function should be loaded by the Python caller
    let modalElements = {};
    try {
        if (typeof detectModalElements === 'function') {
            modalElements = detectModalElements();
        } else {
            modalElements = { error: 'detectModalElements function not available', totalElements: 0 };
        }
    } catch (e) {
        modalElements = { 
            error: 'Modal detection failed: ' + e.message, 
            totalElements: 0,
            stack: e.stack 
        };
    }
    
    // === RETURN COMPREHENSIVE RESULT ===
    return {
        // Core content
        title: extractionResult.enhancedTitle || extractionResult.title || 'No Title',
        content: extractionResult.content || 'No content found',
        htmlContent: extractionResult.htmlContent || '',
        
        // Links and navigation
        links: links,
        debug_links: links.slice(0, 5).map(l => `"${l.text.substring(0, 30)}" -> ${l.url}`).join('; '),
        
        // Interactive elements
        modalElements: modalElements,
        pageControls: pageControls,

        // Metadata
        url: window.location.href,
        author: extractionResult.author,
        datePublished: extractionResult.datePublished,
        description: extractionResult.description,
        
        // Extraction metadata for debugging and optimization
        extraction: {
            method: extractionResult.method,
            confidence: extractionResult.confidence,
            source: extractionResult.source,
            allMethods: extractionResult.extractionMethods,
            contentLength: extractionResult.content?.length || 0,
            linkCount: links.length,
            hasStructuredData: !!extractionResult.structuredData
        },
        
        // Section information for combined extractions
        sections: extractionResult.sections
    };
}