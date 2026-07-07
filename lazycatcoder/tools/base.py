"""Enhanced base class for all tools with validation and permissions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Optional


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
    def execute(self, **kwargs) -> str:
        """Run the tool and return a text result."""
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
