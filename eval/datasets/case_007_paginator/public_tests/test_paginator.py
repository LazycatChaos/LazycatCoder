import pytest
from paginator import Paginator


def test_normal_pagination():
    """Normal case: 100 items, page_size 10, page 1."""
    p = Paginator(page=1, page_size=10, total=100)
    assert p.get_offset() == 0
    assert p.get_total_pages() == 10
    meta = p.get_meta()
    assert meta["page"] == 1
    assert meta["total_pages"] == 10
    assert meta["has_next"] is True
    assert meta["has_prev"] is False


def test_total_zero():
    """total=0 should have total_pages=0."""
    p = Paginator(page=1, page_size=10, total=0)
    assert p.get_total_pages() == 0
    meta = p.get_meta()
    assert meta["total_pages"] == 0
    assert meta["has_next"] is False
    assert meta["has_prev"] is False


def test_has_next_has_prev():
    """has_next and has_prev should be correct on first, middle, last pages."""
    # First page
    p1 = Paginator(page=1, page_size=10, total=30)
    assert p1.get_meta()["has_prev"] is False
    assert p1.get_meta()["has_next"] is True

    # Middle page
    p2 = Paginator(page=2, page_size=10, total=30)
    assert p2.get_meta()["has_prev"] is True
    assert p2.get_meta()["has_next"] is True

    # Last page
    p3 = Paginator(page=3, page_size=10, total=30)
    assert p3.get_meta()["has_prev"] is True
    assert p3.get_meta()["has_next"] is False


def test_meta_shape_unchanged():
    """Meta dict should always have the same keys."""
    p = Paginator(page=1, page_size=10, total=100)
    meta = p.get_meta()
    expected_keys = {"page", "page_size", "total", "total_pages", "has_next", "has_prev"}
    assert set(meta.keys()) == expected_keys


def test_single_item():
    """Single item should have 1 page."""
    p = Paginator(page=1, page_size=10, total=1)
    assert p.get_total_pages() == 1
    meta = p.get_meta()
    assert meta["has_next"] is False
    assert meta["has_prev"] is False
