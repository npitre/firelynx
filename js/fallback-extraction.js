// === FALLBACK CONTENT EXTRACTION ===
/**
 * Permissive content extraction that preserves more original HTML structure
 * 
 * Purpose: When advanced extraction methods (Readability.js, semantic scoring) 
 * are too aggressive and filter out important content, this fallback provides
 * a more inclusive approach that prioritizes completeness over precision.
 * 
 * Use case: navigation-dense pages (shopping sites, marketplaces, dashboards)
 * where categories, listings, and links are spread across the page in ways
 * that don't match traditional "article" content patterns.
 */

function extractContentFallback() {
    // Strategy: Extract content from multiple significant page areas
    // without being overly selective about what constitutes "main content"
    
    const contentAreas = [];
    
    // 1. Try semantic HTML5 landmarks first (most reliable)
    const landmarks = ['main', 'section', 'article', 'nav', 'aside'];
    for (const tag of landmarks) {
        const elements = document.querySelectorAll(tag);
        for (const element of elements) {
            const text = element.innerText?.trim() || '';
            if (text.length > 50) {  // Very low threshold - include more content
                contentAreas.push({
                    source: `HTML5 ${tag}`,
                    text: text,
                    html: element.innerHTML,
                    score: getInclusiveScore(element, tag),
                    element: element
                });
            }
        }
    }
    
    // 2. If no semantic elements, look for common content containers
    if (contentAreas.length === 0) {
        const selectors = [
            '[role="main"]', '[role="content"]', 
            '#main', '#content', '.main', '.content',
            '#container', '.container', '#wrapper', '.wrapper',
            'body > div', 'body > section'  // Top-level containers
        ];
        
        for (const selector of selectors) {
            const elements = document.querySelectorAll(selector);
            for (const element of elements) {
                const text = element.innerText?.trim() || '';
                if (text.length > 100) {
                    contentAreas.push({
                        source: `Container: ${selector}`,
                        text: text,
                        html: element.innerHTML,
                        score: getInclusiveScore(element, 'container'),
                        element: element
                    });
                }
            }
        }
    }
    
    // 3. Last resort: Take significant chunks from body
    if (contentAreas.length === 0) {
        const bodyChildren = Array.from(document.body.children);
        for (const child of bodyChildren) {
            const text = child.innerText?.trim() || '';
            if (text.length > 30) {  // Very permissive threshold
                contentAreas.push({
                    source: `Body child: ${child.tagName.toLowerCase()}`,
                    text: text,
                    html: child.innerHTML,
                    score: getInclusiveScore(child, 'body-child'),
                    element: child
                });
            }
        }
    }
    
    // 4. Remove nested duplicates but be more permissive than advanced extraction
    const filteredAreas = removeNestedContent(contentAreas);
    
    // 5. Sort by score but include multiple areas
    filteredAreas.sort((a, b) => b.score - a.score);
    
    // 6. Take top areas (more inclusive than single "best" result)
    const topAreas = filteredAreas.slice(0, 8);  // Much more permissive
    
    if (topAreas.length === 0) {
        return null;
    }
    
    // 7. Combine multiple content areas with clear separators
    const combinedText = topAreas.map((area, index) => {
        const header = `[${area.source.toUpperCase()}]`;
        return `${header}\n${area.text}`;
    }).join('\n\n' + '─'.repeat(60) + '\n\n');
    
    const combinedHtml = topAreas.map(area => 
        `<div class="content-area">${area.html}</div>`
    ).join('<hr class="area-separator">');
    
    return {
        method: 'fallback_extraction',
        confidence: 0.6,  // Lower confidence but higher completeness
        title: document.title || 'Content',
        content: combinedText,
        htmlContent: combinedHtml,
        areas: topAreas.length,
        source: 'Permissive fallback extraction'
    };
}

/**
 * Inclusive scoring system - prioritizes completeness over precision
 * Much more permissive than the advanced semantic scoring
 */
function getInclusiveScore(element, context) {
    let score = 20;  // Base score for any content
    
    // Boost based on context
    const contextBonus = {
        'main': 50, 'article': 40, 'section': 35, 'nav': 25, 'aside': 15,
        'container': 30, 'body-child': 10
    };
    score += contextBonus[context] || 0;
    
    // Boost for substantial text content (but don't penalize shorter content too much)
    const textLength = element.innerText?.trim().length || 0;
    if (textLength > 100) score += 20;
    if (textLength > 500) score += 30;
    if (textLength > 1000) score += 40;
    
    // Boost for links (opposite of advanced extraction - links often indicate navigation/categories)
    const linkCount = element.querySelectorAll('a').length;
    if (linkCount > 0) score += Math.min(linkCount * 2, 20);  // Don't over-penalize link-heavy areas
    
    // Boost for semantic class names
    const className = (element.className || '').toLowerCase();
    const positiveIndicators = [
        'content', 'main', 'section', 'article', 'post', 'page',
        'navigation', 'nav', 'menu', 'sidebar', 'header', 'footer',
        'product', 'item', 'listing', 'category', 'featured'
    ];
    
    for (const indicator of positiveIndicators) {
        if (className.includes(indicator)) {
            score += 15;
        }
    }
    
    // Only penalize obviously problematic elements
    const negativeIndicators = ['ad', 'advertisement', 'popup', 'modal', 'overlay'];
    for (const indicator of negativeIndicators) {
        if (className.includes(indicator)) {
            score -= 30;
        }
    }
    
    return score;
}

/**
 * Remove nested content but be more permissive than advanced extraction
 */
function removeNestedContent(contentAreas) {
    const filtered = [];
    
    for (const area of contentAreas) {
        let isNested = false;
        
        // Only remove if completely contained within another area
        for (const existing of filtered) {
            if (existing.element.contains(area.element)) {
                isNested = true;
                break;
            }
            // If this area contains an existing one, remove the existing one
            if (area.element.contains(existing.element)) {
                const index = filtered.indexOf(existing);
                filtered.splice(index, 1);
            }
        }
        
        if (!isNested) {
            filtered.push(area);
        }
    }
    
    return filtered;
}

/**
 * Execute fallback extraction - main entry point
 */
function executeFallbackExtraction() {
    const extractionResult = extractContentFallback();
    
    if (!extractionResult) {
        return {
            title: document.title || 'No Title',
            content: 'No content could be extracted',
            htmlContent: '',
            links: [],
            url: window.location.href,
            extraction: {
                method: 'fallback_failed',
                confidence: 0,
                source: 'Fallback extraction failed'
            }
        };
    }
    
    // Extract links from all content areas (more permissive)
    const allLinks = [];
    const linkElements = document.querySelectorAll('a[href]');
    
    for (const link of linkElements) {
        const text = (link.innerText || link.textContent || '').trim();
        const url = link.href;
        
        if (text && url && !url.startsWith('javascript:') && text.length > 0) {
            // Be more permissive about what constitutes a valid link
            allLinks.push({ text, url });
        }
    }
    
    return {
        // Core content
        title: extractionResult.title,
        content: extractionResult.content,
        htmlContent: extractionResult.htmlContent,
        
        // Links - include more links for better navigation
        links: allLinks,
        debug_links: allLinks.slice(0, 10).map(l => `"${l.text.substring(0, 30)}" -> ${l.url}`).join('; '),
        
        // Metadata
        url: window.location.href,
        
        // Extraction metadata
        extraction: {
            method: extractionResult.method,
            confidence: extractionResult.confidence,
            source: extractionResult.source,
            contentLength: extractionResult.content?.length || 0,
            linkCount: allLinks.length,
            areas: extractionResult.areas
        }
    };
}