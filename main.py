"""CleanWispr launcher — run with:  python main.py

Works without installing the package: Python adds this file's folder to the
import path, so the cleanwispr package next to it is found automatically.
(The Windows autostart entry also uses this file for the same reason.)
"""

from cleanwispr.app import main

if __name__ == "__main__":
    raise SystemExit(main())
