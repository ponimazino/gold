"""Shared configuration."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

SYMBOL = "XAU/USD"
INTERVALS = ["1h", "4h"]

# WIB (Asia/Jakarta, UTC+7, no DST) is the *display* timezone.
# All stored timestamps stay UTC so backtests never fight a timezone offset.
DISPLAY_TZ = "Asia/Jakarta"

# Twelve Data free plan: 8 credits/min, 800 credits/day,
# and a 5,000-data-point cap per time_series request.
MAX_OUTPUT_SIZE = 5000
DEFAULT_BACKFILL_YEARS = 3