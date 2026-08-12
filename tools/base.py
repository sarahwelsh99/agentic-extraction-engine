"""Base class for all agent tools.

All tools inherit from this class to ensure consistent interface,
error handling, and response formatting.
"""
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Literal

logger = logging.getLogger(__name__)


class ToolResponse(dict):
    """Standard response format for all tools.

    Ensures consistent structure across all tools with:
    - status: success, error, partial_success, warning
    - error: None if no error occurred
    - timestamp: ISO 8601 timestamp
    - tool-specific fields
    """

    def __init__(self, status: Literal["success", "error", "partial_success", "warning"] = "error",
                 error: str = None, **kwargs):
        """Initialize tool response.

        Args:
            status: Response status
            error: Error message (None if no error)
            **kwargs: Tool-specific response fields
        """
        super().__init__(**kwargs)
        self["status"] = status
        self["error"] = error
        self["timestamp"] = datetime.now(timezone.utc).isoformat()


class AgentTool(ABC):
    """Base class for all agent tools.

    Provides:
    - Standard interface (execute, validate)
    - Consistent error handling
    - JSON serialization
    - Type checking
    - Logging
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name (must be unique across all tools)."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description of what the tool does."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Dict[str, Any]:
        """JSONSchema for input validation.

        Returns:
            Dict with keys: properties, required, type, description
        """
        pass

    @property
    @abstractmethod
    def output_schema(self) -> Dict[str, Any]:
        """JSONSchema for output structure.

        Returns:
            Dict with keys: properties, required, type, description
        """
        pass

    @abstractmethod
    def execute(self, input_data: Dict[str, Any]) -> ToolResponse:
        """Execute the tool.

        Args:
            input_data: Dictionary matching input_schema

        Returns:
            ToolResponse with status, error, and tool-specific fields
        """
        pass

    def validate_input(self, input_data: Dict[str, Any]) -> tuple[bool, str]:
        """Validate input against schema.

        Args:
            input_data: Dictionary to validate

        Returns:
            Tuple of (is_valid: bool, error_message: str or None)
        """
        required = self.input_schema.get("required", [])
        for field in required:
            if field not in input_data:
                return False, f"Missing required field: {field}"
        return True, None

    def __call__(self, input_data: Dict[str, Any]) -> str:
        """Call the tool and return JSON response.

        Args:
            input_data: Dictionary matching input_schema

        Returns:
            JSON string with tool response
        """
        try:
            # Validate input
            is_valid, error_msg = self.validate_input(input_data)
            if not is_valid:
                response = ToolResponse(status="error", error=error_msg)
                logger.error(f"{self.name} validation error: {error_msg}")
                return json.dumps(response)

            # Execute tool
            logger.info(f"Executing tool: {self.name}")
            response = self.execute(input_data)

            # Ensure response has required fields
            if not isinstance(response, ToolResponse):
                response = ToolResponse(**response)

            return json.dumps(response)

        except Exception as e:
            logger.error(f"{self.name} error: {str(e)}", exc_info=True)
            response = ToolResponse(status="error", error=str(e))
            return json.dumps(response)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name}>"
