"""
Device detection utility for Flask app
Detects mobile vs desktop devices based on User-Agent and screen size
"""

import re
from flask import request


def is_mobile_device():
    """
    Detect if the current request is from a mobile device
    Returns True for mobile devices, False for desktop
    """
    user_agent = request.headers.get('User-Agent', '').lower()
    
    # Mobile device patterns
    mobile_patterns = [
        r'mobile',
        r'android',
        r'iphone',
        r'ipad',
        r'ipod',
        r'blackberry',
        r'windows phone',
        r'opera mini',
        r'iemobile',
        r'kindle',
        r'silk',
        r'fennec',
        r'minimo',
        r'palm',
        r'pocket',
        r'psp',
        r'webos',
        r'maemo',
        r'maemo',
        r'netfront',
        r'opera mobi',
        r'opera mini',
        r'polaris',
        r'risc os',
        r'symbian',
        r'up\.browser',
        r'up\.link',
        r'vodafone',
        r'wap',
        r'windows ce',
        r'xda',
        r'xiino'
    ]
    
    # Check if any mobile pattern matches
    for pattern in mobile_patterns:
        if re.search(pattern, user_agent):
            return True
    
    return False


def get_device_type():
    """
    Get device type as string
    Returns 'mobile' or 'desktop'
    """
    return 'mobile' if is_mobile_device() else 'desktop'


def get_template_suffix():
    """
    Get template suffix for device-specific templates
    Returns '_mobile' for mobile devices, '' for desktop
    """
    return '_mobile' if is_mobile_device() else ''
