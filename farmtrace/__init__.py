"""FarmTrace — Farm Produce Transportation and Traceability Management System.

The package is organised into layers:

* ``farmtrace.config``          – environment / Streamlit-secrets configuration
* ``farmtrace.database``        – SQLAlchemy models, connection handling, repositories
* ``farmtrace.authentication``  – password hashing, login, role checks
* ``farmtrace.services``        – business rules (all permission checks live here)
* ``farmtrace.components``      – Streamlit presentation helpers
* ``farmtrace.utils``           – validation, formatting and logging helpers
"""

__version__ = "1.0.0"
APP_NAME = "FarmTrace"
APP_TITLE = "Farm Produce Transportation and Traceability Management System"
