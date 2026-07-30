class Paginator:
    """Handles pagination for API responses."""

    DEFAULT_PAGE_SIZE = 20

    def __init__(self, page: int, page_size: int, total: int):
        self.total = total

        # Validate page_size: must be > 0, otherwise use default
        if page_size is None or page_size <= 0:
            self.page_size = self.DEFAULT_PAGE_SIZE
        else:
            self.page_size = page_size

        # Validate page: must be >= 1
        if page is None or page < 1:
            self.page = 1
        else:
            self.page = page

    def get_offset(self) -> int:
        """Calculate the offset for database query."""
        return (self.page - 1) * self.page_size

    def get_total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.total <= 0:
            return 0
        # Use ceiling division
        return (self.total + self.page_size - 1) // self.page_size

    def get_meta(self) -> dict:
        """Return pagination metadata."""
        total_pages = self.get_total_pages()

        # Clamp page to valid range for meta display
        display_page = self.page
        if display_page > total_pages and total_pages > 0:
            display_page = total_pages
        elif total_pages == 0:
            display_page = 0

        return {
            "page": display_page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": total_pages,
            "has_next": display_page < total_pages,
            "has_prev": display_page > 1,
        }
