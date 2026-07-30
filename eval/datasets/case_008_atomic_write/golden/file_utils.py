import json
import os
import tempfile


def write_json_atomic(path, data):
    """Write JSON data to a file atomically.
    
    Writes to a temporary file first, then atomically replaces the target.
    If writing fails, the temporary file is cleaned up.
    If the target file exists, its permissions are preserved.
    """
    path = os.path.abspath(path)
    dir_name = os.path.dirname(path)
    
    # Preserve existing file permissions if target exists
    target_mode = None
    if os.path.exists(path):
        target_mode = os.stat(path).st_mode
    
    # Create temp file in the same directory (for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=dir_name,
        prefix=".tmp_",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Preserve permissions if target existed
        if target_mode is not None:
            os.chmod(tmp_path, target_mode)
        
        # Atomic replace
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
