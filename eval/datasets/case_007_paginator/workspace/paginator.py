class Paginator:
    """Handles pagination for API responses."""

    def __init__(self, page: int, page_size: int, total: int):
        self.page = page
        self.page_size = page_size
        self.total = total

    def get_offset(self) -> int:
        """Calculate the offset for database query."""
        return (self.page - 1) * self.page_size

    def get_total_pages(self) -> int:
        """Calculate total number of pages."""
        return self.total // self.page_size

    def get_meta(self) -> dict:
        """Return pagination metadata."""
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.get_total_pages(),
            "has_next": self.page < self.get_total_pages(),
            "has_prev": self.page > 1,
        }
