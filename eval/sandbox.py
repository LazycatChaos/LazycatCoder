"""Sandbox: create isolated temp workspace from golden copy, then clean up."""

import shutil
import tempfile
from pathlib import Path


class Sandbox:
    """Create a temporary copy of a case's workspace for safe Agent execution.

    Usage:
        sandbox = Sandbox(case_dir)
        sandbox.setup()
        # ... run agent against sandbox.workdir ...
        sandbox.teardown()
    """

    def __init__(self, case_dir: str | Path):
        self.case_dir = Path(case_dir)
        self.workspace_src = self.case_dir / "workspace"
        self.public_tests_src = self.case_dir / "public_tests"
        self.hidden_tests_src = self.case_dir / "hidden_tests"
        self._tmpdir: tempfile.TemporaryDirectory | None = None
        self.workdir: Path | None = None

    def setup(self) -> Path:
        """Copy workspace → temp dir. Also copies public_tests if they exist.
        
        Returns the temp workdir path.
        """
        if not self.workspace_src.exists():
            raise FileNotFoundError(f"Workspace not found: {self.workspace_src}")

        self._tmpdir = tempfile.TemporaryDirectory()
        dest = Path(self._tmpdir.name)

        # Copy all files from workspace into temp dir
        for item in self.workspace_src.iterdir():
            src_item = item
            dst_item = dest / item.name
            if item.is_dir():
                shutil.copytree(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)

        # Copy public tests if they exist (Agent can see and run these)
        if self.public_tests_src.exists():
            for item in self.public_tests_src.iterdir():
                dst = dest / item.name
                if item.is_file():
                    shutil.copy2(item, dst)

        self.workdir = dest
        return dest

    def inject_tests(self) -> Path:
        """Copy hidden_tests into the sandbox workdir (post-run injection).

        Returns the path where tests were injected.
        """
        if not self.workdir:
            raise RuntimeError("Call setup() before inject_tests()")
        if not self.hidden_tests_src.exists():
            raise FileNotFoundError(f"Hidden tests not found: {self.hidden_tests_src}")

        for item in self.hidden_tests_src.iterdir():
            dst = self.workdir / item.name
            if item.is_file():
                shutil.copy2(item, dst)

        return self.workdir

    def teardown(self):
        """Remove the temporary directory."""
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None
            self.workdir = None
