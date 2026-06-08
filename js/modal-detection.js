/**
 * Modal Dialog Detection System
 * 
 * IMPORTANT: This JavaScript runs in Firefox and should NEVER reference the proxy server.
 * It only detects and catalogs modal elements for the proxy to handle.
 * 
 * Architecture:
 * 1. Firefox detects modals and buttons
 * 2. Returns data about detected elements to Python
 * 3. Python/proxy generates lynx-friendly forms
 * 4. When forms submitted, Python tells Firefox to click original elements
 */

/**
 * Detect JavaScript-driven modal dialogs and their buttons
 * Returns data about found elements WITHOUT generating proxy-referencing forms
 *
 * IMPORTANT: Only detects buttons INSIDE modal dialogs, not top-level navigation buttons
 */
function detectModalElements() {
    const detectedElements = {
        modals: [],
        buttons: [],
        totalElements: 0
    };

    // 1. First, find all visible modal dialogs
    const dialogs = document.querySelectorAll('[role="dialog"], .modal, [aria-modal="true"]');
    const visibleDialogs = [];

    for (let i = 0; i < dialogs.length; i++) {
        const dialog = dialogs[i];
        const style = getComputedStyle(dialog);

        // Check if dialog is actually visible
        if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
            visibleDialogs.push(dialog);
        }
    }

    // 2. For each visible dialog, detect buttons inside it
    for (let i = 0; i < visibleDialogs.length; i++) {
        const dialog = visibleDialogs[i];
        const dialogButtons = Array.from(dialog.querySelectorAll('button, input[type="submit"], [role="button"]'));

        // Filter to only visible buttons within this dialog
        const visibleButtons = dialogButtons.filter(btn => {
            const style = getComputedStyle(btn);
            return style.display !== 'none' && style.visibility !== 'hidden' &&
                   style.opacity !== '0' && btn.offsetParent !== null;
        });

        if (visibleButtons.length > 0) {
            // Create a modal entry for this dialog
            const dialogId = `modal-dialog-${Date.now()}-${i}`;
            dialog.setAttribute('data-modal-id', dialogId);

            detectedElements.modals.push({
                elementId: dialogId,
                name: 'generic_dialog',
                buttonCount: visibleButtons.length,
                visible: true
            });

            // Process each button inside this dialog
            for (let j = 0; j < visibleButtons.length; j++) {
                const button = visibleButtons[j];
                const buttonText = (button.innerText || button.textContent || '').trim();

                if (buttonText && buttonText.length > 0 && buttonText.length < 50) {
                    // Generate unique ID for this button
                    const elementId = `modal-btn-${Date.now()}-${i}-${j}`;

                    // Store reference on the element for later clicking
                    button.setAttribute('data-modal-id', elementId);

                    // Determine action type based on button text
                    let action = 'click_button';
                    const lowerText = buttonText.toLowerCase();

                    // Map common button patterns to semantic actions
                    if (lowerText.includes('ok') || lowerText.includes('accept')) {
                        action = 'accept';
                    } else if (lowerText.includes('cancel') || lowerText.includes('close')) {
                        action = 'cancel';
                    } else if (lowerText.includes('allow') || lowerText.includes('permit')) {
                        action = 'allow_notifications';
                    } else if (lowerText.includes('block') || lowerText.includes('deny')) {
                        action = 'block_notifications';
                    } else if (lowerText.includes('continue') || lowerText.includes('proceed')) {
                        action = 'continue';
                    }

                    detectedElements.buttons.push({
                        elementId: elementId,
                        text: buttonText,
                        action: action,
                        tagName: button.tagName.toLowerCase(),
                        isVisible: true,
                        dialogId: dialogId  // Link button to its parent dialog
                    });
                }
            }
        }
    }

    detectedElements.totalElements = detectedElements.buttons.length + detectedElements.modals.length;
    return detectedElements;
}

/**
 * Click an element by its stored modal ID
 * This function is called by the Python proxy when a form is submitted
 */
function clickModalElement(elementId, actionType) {
    try {
        let clicked = false;
        
        // Strategy 1: Use stored element ID
        if (elementId) {
            const element = document.querySelector(`[data-modal-id="${elementId}"]`);
            if (element && element.offsetParent !== null) {
                element.click();
                clicked = true;
                // Successfully clicked element via stored ID
                return { success: true, method: 'stored_id', element: element.tagName };
            }
        }
        
        // Strategy 2: Fallback to text-based search if ID method failed
        if (!clicked && actionType) {
            const allButtons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]'));
            const searchText = actionType.replace('_', ' ').toLowerCase();
            
            for (const btn of allButtons) {
                const text = (btn.textContent || btn.innerText || '').toLowerCase();
                
                if (text.includes(searchText) || actionType === 'click_button') {
                    btn.click();
                    clicked = true;
                    console.log('Clicked element via text search:', btn);
                    return { success: true, method: 'text_search', element: btn.tagName };
                }
            }
        }
        
        return { success: false, reason: 'Element not found or not clickable' };
        
    } catch (e) {
        console.error('Modal click failed:', e);
        return { success: false, reason: e.message };
    }
}