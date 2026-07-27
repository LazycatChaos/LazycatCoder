"""CSV stream export module."""

import csv
import io


def export_csv_stream(data, fieldnames: list[str]):
    """Export data as a CSV stream.

    Args:
        data: An iterable of dicts, each representing a row.
        fieldnames: List of column names for the CSV header.

    Yields:
        CSV-formatted string chunks.
    """
    # Collect all data first
    rows = list(data)

    # Write header
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    yield output.getvalue()

    # Write rows
    for row in rows:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writerow(row)
        yield output.getvalue()
