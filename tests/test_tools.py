"""Tests for the tool system."""

import os
import tempfile
from pathlib import Path

from nanocoder.tools import ALL_TOOLS, get_tool


# --- Registry ---

def test_tool_count():
    """11 tools: bash, read_file, write_file, edit_file, delete_file,
    glob, grep, agent, todo_write, web_search, fetch_url"""
    assert len(ALL_TOOLS) == 11


def test_all_tools_have_valid_schema():
    for t in ALL_TOOLS:
        s = t.schema()
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "parameters" in s["function"]
        params = s["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "required" in params


def test_all_tools_have_execute():
    for t in ALL_TOOLS:
        assert callable(getattr(t, "execute", None)), f"{t.name} missing execute()"


def test_tool_lookup_by_name():
    expected = [
        "bash", "read_file", "write_file", "edit_file", "delete_file",
        "glob", "grep", "agent", "todo_write", "web_search", "fetch_url",
    ]
    for name in expected:
        t = get_tool(name)
        assert t is not None, f"Tool '{name}' not found"
        assert t.name == name


# --- bash ---

def test_bash_basic():
    bash = get_tool("bash")
    r = bash.execute(command="echo hello")
    assert "hello" in r


def test_bash_exit_code():
    bash = get_tool("bash")
    r = bash.execute(command="exit 42")
    assert "exit code: 42" in r


def test_bash_timeout():
    bash = get_tool("bash")
    r = bash.execute(command="sleep 10", timeout=1)
    assert "timed out" in r


def test_bash_blocks_rm_rf():
    bash = get_tool("bash")
    r = bash.execute(command="rm -rf /")
    assert "Blocked" in r


def test_bash_blocks_rm_r_subdir():
    """rm -r on any path (not just root) should be blocked."""
    bash = get_tool("bash")
    r = bash.execute(command="rm -r subdir")
    assert "Blocked" in r


def test_bash_blocks_fork_bomb():
    bash = get_tool("bash")
    r = bash.execute(command=":(){ :|:& };:")
    assert "Blocked" in r


def test_bash_truncates_long_output():
    bash = get_tool("bash")
    # Write a temp script to avoid PowerShell quoting issues
    script = tempfile.mktemp(suffix=".py")
    with open(script, "w") as f:
        f.write("import sys; sys.stdout.write('x' * 20000)\n")
    try:
        r = bash.execute(command=f"python {script}")
        assert "truncated" in r
    finally:
        os.unlink(script)


# --- read_file ---

def test_read_file():
    read = get_tool("read_file")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\n")
        f.flush()
        path = f.name
    # Close the file before reading/unlinking (Windows requires this)
    r = read.execute(file_path=path)
    assert "line1" in r
    assert "line2" in r
    os.unlink(path)


def test_read_file_not_found():
    read = get_tool("read_file")
    r = read.execute(file_path="/tmp/nanocoder_nonexistent_file.txt")
    assert "not found" in r.lower() or "Error" in r


def test_read_file_offset_limit():
    read = get_tool("read_file")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("\n".join(f"line{i}" for i in range(100)))
        f.flush()
        path = f.name
    # Close before reading/unlinking (Windows requires this)
    r = read.execute(file_path=path, offset=10, limit=5)
    # offset is 1-based, so line10 should be in result
    assert "line10" in r or "line9" in r
    os.unlink(path)


# --- write_file ---

def test_write_file():
    write = get_tool("write_file")
    path = tempfile.mktemp(suffix=".txt")
    r = write.execute(file_path=path, content="hello world\n")
    assert "wrote" in r.lower()
    assert Path(path).read_text() == "hello world\n"
    os.unlink(path)


def test_write_file_creates_dirs():
    write = get_tool("write_file")
    path = tempfile.mktemp(suffix=".txt")
    nested = os.path.join(os.path.dirname(path), "sub", "dir", "file.txt")
    r = write.execute(file_path=nested, content="nested\n")
    assert "wrote" in r.lower()
    assert Path(nested).read_text() == "nested\n"
    import shutil
    shutil.rmtree(os.path.join(os.path.dirname(path), "sub"))


# --- edit_file ---

def test_edit_file_basic():
    edit = get_tool("edit_file")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def foo():\n    return 42\n")
        f.flush()
        path = f.name
    # Close before editing (Windows requires this)
    # First record the file as "read" so edit validation passes
    from nanocoder.tools.edit import record_file_read
    record_file_read(path)
    r = edit.execute(file_path=path, command="replace", old_string="return 42", new_string="return 99")
    assert "Edited" in r or "Modified" in r or "Replaced" in r
    content = Path(path).read_text()
    assert "return 99" in content
    assert "return 42" not in content
    os.unlink(path)


def test_edit_file_not_found_string():
    edit = get_tool("edit_file")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("hello\n")
        f.flush()
        path = f.name
    from nanocoder.tools.edit import record_file_read
    record_file_read(path)
    r = edit.execute(file_path=path, command="replace", old_string="NONEXISTENT", new_string="x")
    assert "not found" in r.lower()
    os.unlink(path)


def test_edit_file_duplicate_string():
    edit = get_tool("edit_file")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("dup\ndup\n")
        f.flush()
        path = f.name
    from nanocoder.tools.edit import record_file_read
    record_file_read(path)
    r = edit.execute(file_path=path, command="replace", old_string="dup", new_string="x")
    assert "2 times" in r or "appears" in r.lower()
    os.unlink(path)


# --- delete_file ---

def test_delete_file_basic():
    delete = get_tool("delete_file")
    # Create temp file inside workdir (delete_file restricts to workdir)
    workdir = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(workdir, ".test_delete_temp.txt")
    with open(path, "w") as f:
        f.write("delete me")
    r = delete.execute(file_path=path)
    assert "Deleted" in r
    assert not os.path.exists(path)


def test_delete_file_not_found():
    delete = get_tool("delete_file")
    r = delete.execute(file_path="/nonexistent_file_xyz.txt")
    assert "not found" in r.lower() or "Error" in r


def test_delete_file_rejects_directory():
    """delete_file must refuse to delete directories."""
    delete = get_tool("delete_file")
    r = delete.execute(file_path=tempfile.gettempdir())
    assert "Cannot delete directory" in r or "Error" in r


def test_delete_file_workdir_sandbox():
    """delete_file must not allow paths outside workdir."""
    from nanocoder.tools.delete import DeleteFileTool
    t = DeleteFileTool()
    t.workdir = r"D:\pycharm_workspace\NanoCoder"

    # Escape attempt with ..
    r = t.execute(r"..\other_dir\secret.txt")
    assert "outside the working directory" in r or "Error" in r

    # Absolute path outside workdir
    r = t.execute(r"C:\Windows\win.ini")
    assert "outside the working directory" in r or "Error" in r


# --- glob ---

def test_glob_finds_files():
    glob_t = get_tool("glob")
    r = glob_t.execute(pattern="*.py", path=os.path.dirname(__file__))
    assert "test_tools.py" in r or "test_core.py" in r


def test_glob_no_match():
    glob_t = get_tool("glob")
    r = glob_t.execute(pattern="*.nonexistent_extension_xyz")
    assert "No files" in r


# --- grep ---

def test_grep_finds_pattern():
    grep = get_tool("grep")
    # Use content mode to get actual matching lines
    r = grep.execute(pattern="def test_grep", path=os.path.dirname(__file__), output_mode="content")
    assert "test_grep" in r


def test_grep_invalid_regex():
    grep = get_tool("grep")
    r = grep.execute(pattern="[invalid")
    # ripgrep returns "No files found" or an error for invalid regex
    assert "No files found" in r or "Error" in r or "Invalid" in r


def test_grep_nonexistent_path():
    grep = get_tool("grep")
    r = grep.execute(pattern="test", path="/nonexistent_dir_abc")
    assert "not found" in r.lower() or "Error" in r


# --- agent tool ---

def test_agent_tool_schema():
    agent_t = get_tool("agent")
    s = agent_t.schema()
    assert s["function"]["name"] == "agent"
    assert "task" in s["function"]["parameters"]["properties"]


# --- todo_write tool ---

def test_todo_tool_schema():
    todo = get_tool("todo_write")
    s = todo.schema()
    assert s["function"]["name"] == "todo_write"
    assert "todos" in s["function"]["parameters"]["properties"]


# --- web_search tool ---

def test_web_search_schema():
    ws = get_tool("web_search")
    s = ws.schema()
    assert s["function"]["name"] == "web_search"
    assert "query" in s["function"]["parameters"]["properties"]


# --- fetch_url tool ---

def test_fetch_url_schema():
    fetch = get_tool("fetch_url")
    s = fetch.schema()
    assert s["function"]["name"] == "fetch_url"
    assert "url" in s["function"]["parameters"]["properties"]
