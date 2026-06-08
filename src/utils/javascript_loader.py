#!/usr/bin/env python3
"""
JavaScript Loader Utility
Handles loading external JavaScript files from the js/ directory
"""

import os
import logging

logger = logging.getLogger(__name__)

class JavaScriptLoader:
    """Utility class for loading JavaScript files"""

    def __init__(self, base_dir=None):
        """
        Initialize JavaScript loader

        Args:
            base_dir: Base directory for the project (defaults to firelynx root)
        """
        if base_dir is None:
            # Default to firelynx root directory
            self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        else:
            self.base_dir = base_dir

        self.js_dir = os.path.join(self.base_dir, 'js')

    def load_file(self, filename):
        """
        Load a JavaScript file from the js/ directory

        Args:
            filename: Name of the JavaScript file (e.g., 'readability.js')

        Returns:
            str: Contents of the JavaScript file

        Raises:
            FileNotFoundError: If the JavaScript file doesn't exist
            IOError: If there's an error reading the file
        """
        js_path = os.path.join(self.js_dir, filename)

        try:
            with open(js_path, 'r', encoding='utf-8') as f:
                content = f.read()

            logger.debug(f"Loaded JavaScript file: {filename} ({len(content)} chars)")
            return content

        except FileNotFoundError:
            logger.error(f"JavaScript file not found: {js_path}")
            raise FileNotFoundError(f"JavaScript file not found: {filename}")
        except IOError as e:
            logger.error(f"Error reading JavaScript file {js_path}: {e}")
            raise IOError(f"Error reading JavaScript file {filename}: {e}")

    def load_multiple(self, filenames):
        """
        Load multiple JavaScript files and combine them

        Args:
            filenames: List of JavaScript filenames to load

        Returns:
            str: Combined contents of all JavaScript files
        """
        combined_js = ""

        for filename in filenames:
            try:
                js_content = self.load_file(filename)
                combined_js += f"\n// === {filename} ===\n"
                combined_js += js_content
                combined_js += f"\n// === END {filename} ===\n"
            except (FileNotFoundError, IOError) as e:
                logger.warning(f"Failed to load {filename}: {e}")
                # Continue loading other files

        return combined_js

    def get_available_files(self):
        """
        Get list of available JavaScript files in the js/ directory

        Returns:
            list: List of available JavaScript filenames
        """
        try:
            if not os.path.exists(self.js_dir):
                logger.warning(f"JavaScript directory not found: {self.js_dir}")
                return []

            js_files = [f for f in os.listdir(self.js_dir) if f.endswith('.js')]
            js_files.sort()  # Sort for consistent ordering

            logger.debug(f"Available JavaScript files: {js_files}")
            return js_files

        except OSError as e:
            logger.error(f"Error reading JavaScript directory {self.js_dir}: {e}")
            return []


# Convenience functions for common usage patterns
def load_js_file(filename, base_dir=None):
    """
    Convenience function to load a single JavaScript file

    Args:
        filename: Name of the JavaScript file
        base_dir: Optional base directory (defaults to firelynx root)

    Returns:
        str: Contents of the JavaScript file
    """
    loader = JavaScriptLoader(base_dir)
    return loader.load_file(filename)


def load_multiple_js_files(filenames, base_dir=None):
    """
    Convenience function to load multiple JavaScript files

    Args:
        filenames: List of JavaScript filenames to load
        base_dir: Optional base directory (defaults to firelynx root)

    Returns:
        str: Combined contents of all JavaScript files
    """
    loader = JavaScriptLoader(base_dir)
    return loader.load_multiple(filenames)