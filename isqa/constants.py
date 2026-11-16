"""Provides module specific constants."""

import os
from pathlib import Path

# BASEDIR = os.path.abspath(os.path.dirname(__file__))
BASEDIR = os.path.join(Path(__file__).resolve().parent.parent)
LOGGER_NAME = "isqa"
LOG_FILE_NAME = "isqa.log"
APP_LOG_LEVEL = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
DEFAULT_ENV_EXTRA_CONFIG = "extra.env"
# pause between single reorder API calls (seconds), to not overload the server
ISSUES_REORDER_DELAY_SECONDS = float(os.environ.get("ISQA_REORDER_DELAY_SECONDS", "0.2"))
# max. number of sort passes per board list (normally one pass is enough)
ISSUES_REORDER_MAX_PASSES = 3

LOG_FILE_PATH = os.path.join(BASEDIR, LOG_FILE_NAME)
JSON_FILE_DUMPS_PATH = os.path.join(BASEDIR, ".jsons")
# email templates are shipped inside the package; can be overridden via env
EMAIL_TEMPLATES_DIR = os.environ.get(
    "ISQA_EMAIL_TEMPLATES_DIR",
    os.path.join(Path(__file__).resolve().parent, "templates"),
)
EMAIL_TEMPLATES_PATH = EMAIL_TEMPLATES_DIR
