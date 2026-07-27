# CSV Stream Export Refactor

You are refactoring a CSV export function for a web API.

## Current Code

`csv_export.py` has an `export_csv_stream(data, fieldnames)` function that exports data to CSV.

## Problem

The current implementation **collects all data into a list first** (`list(data)`), which defeats the purpose of streaming. For large datasets (millions of rows), this causes:
1. **High memory usage** — all rows loaded into RAM at once.
2. **High latency** — user waits for all data before receiving any output.
3. **No partial output** — if an error occurs mid-export, nothing is returned.

## Requirements

1. Use `yield` to stream CSV chunks lazily — **do not** call `list(data)` or collect all rows.
2. The first yielded chunk should be the CSV header.
3. Each subsequent yielded chunk should be one CSV row.
4. The function must accept any iterable (list, generator, iterator).
5. Use Python's `csv.DictWriter` for proper CSV formatting (quoting, escaping).
   Rows with extra fields not in `fieldnames` should be handled gracefully
   (ignore extra fields, do not raise).
6. Do not change the function name, module name, or signature.

## Example

```python
def data_source():
    yield {"id": 1, "name": "Alice"}
    yield {"id": 2, "name": "Bob"}

# Each call to next() processes one row — memory efficient!
for chunk in export_csv_stream(data_source(), fieldnames=["id", "name"]):
    print(chunk)
    # Output:
    # id,name\r\n
    # 1,Alice\r\n
    # 2,Bob\r\n
```

## Notes

- This function is designed to be used with `StreamingResponse` (FastAPI) or `StreamingHttpResponse` (Django).
- Only use Python standard library modules.
