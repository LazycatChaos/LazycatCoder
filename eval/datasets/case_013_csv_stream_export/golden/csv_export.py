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
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    yield output.getvalue()

    for row in data:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writerow(row)
        yield output.getvalue()
