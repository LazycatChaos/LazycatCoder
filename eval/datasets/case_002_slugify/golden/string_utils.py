import re
import unicodedata


def slugify(text: str) -> str:
    """Convert a string to a URL-friendly slug.

    - Lowercase
    - Remove special characters (not replaced with hyphens)
    - Replace spaces with hyphens
    - Preserve existing hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Unicode chars are normalized to ASCII equivalents
    """
    if not text:
        return ""

    # Normalize unicode to ASCII (Café → Cafe)
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')

    # Convert to lowercase
    text = text.lower()

    # Remove all non-alphanumeric, non-space, non-hyphen characters
    text = re.sub(r'[^a-z0-9\s-]', '', text)

    # Replace spaces (one or more) with a single hyphen
    text = re.sub(r'\s+', '-', text)

    # Collapse consecutive hyphens into one
    text = re.sub(r'-+', '-', text)

    # Strip leading/trailing hyphens
    text = text.strip('-')

    return text
