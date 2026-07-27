"""System configuration.

All time-related operations should use the TIMEZONE setting below.
The system runs in UTC — do not change this unless you know what you're doing.
"""

# System timezone — all date calculations must use this
TIMEZONE = "UTC"

# Cache TTL in seconds (1 hour)
CACHE_TTL = 3600

# Report format
REPORT_DATE_FORMAT = "%Y-%m-%d"
