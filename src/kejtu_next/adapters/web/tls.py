"""Platform trust for fixed-origin search requests; never relax TLS checks."""
import ssl
import sys
from urllib.request import HTTPSHandler

from ...core.records import Failure


def platform_https_handlers():
    if sys.platform != 'win32':
        return ()
    try:
        import certifi
        # Keep default OS/enterprise roots, then add the release's pinned public
        # roots. A fresh Windows store may not yet contain a site's issuer. No
        # certificate is downloaded and a missing bundle is not a trust fallback.
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ValueError, AttributeError):
        raise Failure('web_unavailable',
            'Secure search is unavailable. Repair the Kejtu installation and try again.') from None
    return (HTTPSHandler(context=context),)
