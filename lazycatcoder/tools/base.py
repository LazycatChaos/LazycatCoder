"""Enhanced base class for all tools with validation and permissions."""

import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Literal, Optional


@dataclass
class ToolResult:
    """Structured result from tool execution.

    Replaces raw string returns so that:
    - success/failure is unambiguous (no string-prefix guessing)
    - error_code enables smart retry strategies
    - meta carries structured metadata for stats/debugging
    - __str__ provides backward-compatible text output
    """
    success: bool
    content: str
    error_code: int = 0
    meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        """Backward-compatible: str(result) returns the content."""
        return self.content

    @classmethod
    def ok(cls, content: str, **meta) -> "ToolResult":
        """Convenience factory for successful results."""
        return cls(success=True, content=content, meta=meta)

    @classmethod
    def fail(cls, content: str, error_code: int = 0, **meta) -> "ToolResult":
        """Convenience factory for failure results."""
        return cls(success=False, content=content, error_code=error_code, meta=meta)


@dataclass
class ValidationResult:
    """Result of input validation."""
    valid: bool
    message: str = ""
    error_code: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class PermissionDecision:
    """Permission decision for tool execution."""
    behavior: Literal["allow", "deny", "ask", "passthrough"] = "allow"
    message: str = ""
    updated_input: Optional[dict] = None


def safe_execute(func):
    """Decorator that catches unhandled exceptions and returns a structured ToolResult.

    This ensures no tool crash can escape into the agent loop.
    error_code=999 signals an uncaught internal crash (vs. expected failures).
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            # If the function already returns a ToolResult, pass it through
            if isinstance(result, ToolResult):
                return result
            # If it returns a string, wrap it as a successful result
            return ToolResult(success=True, content=str(result))
        except Exception as e:
            error_msg = f"Error: Tool internal crash: {str(e)}\n{traceback.format_exc()}"
            return ToolResult(
                success=False,
                content=error_msg,
                error_code=999,
                meta={"exception_type": str(type(e).__name__)},
            )
    return wrapper


class Tool(ABC):
    """Enhanced tool base class with validation and permission support."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the function args

    # Optional metadata
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    max_result_size_chars: int = 100_000
    search_hint: str = ""  # Short hint for tool search UI

    def validate_input(self, **kwargs) -> ValidationResult:
        """
        Validate input parameters before execution.
        Override in subclasses for custom validation.
        """
        return ValidationResult(valid=True)

    def check_permissions(self, **kwargs) -> PermissionDecision:
        """
        Check if the tool execution is permitted.
        Override in subclasses for custom permission logic.
        """
        return PermissionDecision(behavior="allow")

    @property
    def is_read_only(self) -> bool:
        """Return True if this tool doesn't modify state (safe for parallel execution)."""
        return False

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Run the tool and return a structured result."""
        ...

    def schema(self) -> dict:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_auto_classifier_input(self, kwargs: dict) -> str:
        """
        Convert tool input to a string for auto-classification.
        Override in subclasses for better classification.
        """
        return str(kwargs)

    def get_activity_description(self, kwargs: dict) -> str:
        """
        Get a short description of what the tool is doing.
        Used for UI progress indicators.
        """
        return f"Executing {self.name}"
