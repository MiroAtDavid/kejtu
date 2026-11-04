"""Module entry delegates to explicit composition; imports perform no IO."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    if sys.argv[1:] == ['--skill-import-worker']:
        from .bootstrap import skill_import_main
        sys.exit(skill_import_main())
    if len(sys.argv) > 1 and sys.argv[1] == 'native-key':
        from .bootstrap import native_key_main
        sys.exit(native_key_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'native-server':
        from .bootstrap import native_main
        sys.exit(native_main(sys.argv[2:]))
    from .bootstrap import main
    sys.exit(main())
