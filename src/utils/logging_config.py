#!/usr/bin/env python3
"""
Logging Configuration Utility

Logging setup for Firelynx lives inline in main.py (terminal-aware formatting,
log file selection). This module holds the one piece worth sharing: quieting
the noisy third-party loggers.
"""

import logging


def set_selenium_log_level(level=logging.WARNING):
    """
    Configure Selenium logging to reduce noise

    Args:
        level: Logging level for Selenium (default: WARNING)
    """
    selenium_loggers = [
        'selenium',
        'selenium.webdriver',
        'selenium.webdriver.remote',
        'selenium.webdriver.remote.remote_connection',
        'urllib3',
        'urllib3.connectionpool'
    ]

    for logger_name in selenium_loggers:
        logging.getLogger(logger_name).setLevel(level)
