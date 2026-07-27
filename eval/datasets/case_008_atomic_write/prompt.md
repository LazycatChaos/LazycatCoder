# Atomic Write: Fix JSON File Writes

## Background

We have a utility function `write_json_atomic(path, data)` that writes JSON data
to a file. Currently it writes directly to the target file, which means if the
process is interrupted (e.g. crash, power loss), the file will be left in a
corrupted/partial state.

## Task

Convert `write_json_atomic` to use a true atomic write pattern: write to a
temporary file first, then atomically replace the target file.

## Requirements

1. **Write to temp file first**: Create a temporary file in the same directory
   as the target file, write JSON data to it.
2. **Atomic replace**: Use `os.replace()` to atomically move the temp file to
   the target path.
3. **Cleanup on failure**: If writing fails, the temporary file must be removed.
   No leftover temp files.
4. **Preserve permissions**: If the target file already exists, preserve its
   file permissions after replacement.
5. **Unicode support**: Must handle Unicode data correctly.
6. **Backward compatibility**: Keep the same function signature.
7. **Atomic guarantee**: If any step fails during the write process, the target
   file must remain intact with its original content. Never leave the file in a
   truncated, empty, or partially-written state.

## What NOT to change

- Do not change the function signature: `write_json_atomic(path, data)`.
- Do not change the JSON output format (indent, sort_keys, etc.).
