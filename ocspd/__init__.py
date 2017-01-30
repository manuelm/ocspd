# -*- coding: utf-8 -*-
"""
Initialise the ocspd module.

This file only contains some variables we need in the ``ocspd`` name space.
"""
#: The extensions the daemon will try to parse as certificate files
FILE_EXTENSIONS_DEFAULT = 'crt,pem,cer'

#: The default refresh interval for the
#: :class:`ocspd.core.certfinder.CertFinderThread`.
DEFAULT_REFRESH_INTERVAL = 60

#: How many times should we restart threads that crashed.
MAX_RESTART_THREADS = 3
