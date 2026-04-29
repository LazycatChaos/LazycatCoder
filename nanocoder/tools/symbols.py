"""Get file symbols tool.

Extracts class and function signatures from a Python file using AST,
allowing the agent to understand a file's structure on demand without
reading the full code.
"""

import ast
from pathlib import Path
from typing import Optional
from .base import Tool, ValidationResult


class GetFileSymbolsTool(Tool):
    """Get the AST symbol skeleton (classes, functions) of a Python file."""

    name = "get_file_symbols"
    description = (
        "Extract class and function signatures from a Python file using AST. "
        "Returns a compact skeleton showing the file's structure: class names, "
        "method names, and top-level function names. "
        "Use this to quickly understand a file's API without reading the full code. "
        "Especially useful for large files or when scanning multiple files."
    )

    search_hint = "file structure, AST, class methods, function signatures, symbols"

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    parameters = {
        "type": "object",
        "properties": {
            "filepath": {
                "type": "string",
                "description": "Path to the Python file (relative to working directory or absolute).",
            },
            "include_private": {
                "type": "boolean",
                "description": "Include private methods (starting with _). Default: false.",
            },
        },
        "required": ["filepath"],
    }

    def validate_input(self, **kwargs) -> ValidationResult:
        filepath = kwargs.get("filepath")
        if not filepath:
            return ValidationResult(
                valid=False,
                message="Error: 'filepath' parameter is required.",
                error_code=1,
            )
        p = Path(filepath).expanduser().resolve()
        if not p.exists():
            return ValidationResult(
                valid=False,
                message=f"Error: File does not exist: {filepath}",
                error_code=2,
            )
        if p.suffix != ".py":
            return ValidationResult(
                valid=False,
                message=f"Error: Only Python files (.py) are supported. Got: {p.suffix or '(no extension)'}",
                error_code=3,
            )
        return ValidationResult(valid=True)

    def execute(
        self,
        filepath: str,
        include_private: bool = False,
    ) -> str:
        """Extract class and function signatures from a Python file."""
        validation = self.validate_input(filepath=filepath)
        if not validation.valid:
            return validation.message

        p = Path(filepath).expanduser().resolve()

        try:
            source = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(p))
        except SyntaxError as e:
            return f"Error: Failed to parse {filepath}: {e}"
        except (UnicodeDecodeError, OSError) as e:
            return f"Error: Failed to read {filepath}: {e}"

        lines: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                lines.append(f"class {node.name}:")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = sub.name
                        if not include_private and name.startswith("_"):
                            continue
                        args = self._format_args(sub.args)
                        lines.append(f"    def {name}({args})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if not include_private and name.startswith("_"):
                    continue
                args = self._format_args(node.args)
                lines.append(f"def {node.name}({args})")

        if not lines:
            return f"No public symbols found in {filepath}"

        header = f"File symbols: {p}\n"
        return header + "\n".join(lines)

    @staticmethod
    def _format_args(args: ast.arguments) -> str:
        """Format function arguments into a readable string."""
        parts = []
        all_args = list(args.args)
        defaults_offset = len(all_args) - len(args.defaults)

        for i, arg in enumerate(all_args):
            name = arg.arg
            # Skip 'self' and 'cls' for cleaner output
            if name in ("self", "cls"):
                continue
            # Add default value if present
            default_idx = i - defaults_offset
            if default_idx >= 0 and default_idx < len(args.defaults):
                default = args.defaults[default_idx]
                default_str = ast.unparse(default) if hasattr(ast, 'unparse') else "..."
                parts.append(f"{name}={default_str}")
            else:
                parts.append(name)

        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")

        return ", ".join(parts)

    def get_activity_description(self, kwargs: dict) -> str:
        filepath = kwargs.get("filepath", "unknown")
        return f"Inspecting symbols in {filepath}"
