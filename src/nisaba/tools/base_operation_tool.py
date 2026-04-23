"""Operation-dispatch tool pattern.

A tool with a single `operation` string parameter that routes to one of N
named commands. Each operation declares its own parameters; the schema shown
to LLM clients lists the operation menu in the tool description.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from nisaba.tools.base_tool import BaseTool, BaseToolResponse

try:
    from docstring_parser import parse as parse_docstring
    DOCSTRING_PARSER_AVAILABLE = True
except ImportError:
    DOCSTRING_PARSER_AVAILABLE = False


@dataclass(unsafe_hash=True)
class OperationParameter:
    name: str
    type: str
    description: str
    required: bool = False
    default: Any | None = None


@dataclass(unsafe_hash=True)
class Operation:
    command: Callable
    result_formatter: Callable
    name: str
    parameters: dict[str, OperationParameter]
    description: str


class BaseOperationTool(BaseTool):
    def __init__(self):
        self.operations_and_parameters: dict[str, Operation] = self.get_operation_config()

    @classmethod
    def make_operations(cls, operations: list[Operation]) -> dict[str, Operation]:
        return {operation.name: operation for operation in operations}

    @classmethod
    def make_operation(
        cls,
        command: Callable,
        result_formatter: Callable,
        name: str,
        parameters: list[OperationParameter],
        description: str,
    ) -> Operation:
        return Operation(
            command=command,
            result_formatter=result_formatter,
            name=name,
            parameters={p.name: p for p in parameters},
            description=description,
        )

    @classmethod
    def make_parameter(
        cls,
        name: str,
        type: str,
        description: str,
        default: Any | None = None,
        required: bool = False,
    ) -> OperationParameter:
        return OperationParameter(
            name=name,
            type=type,
            description=description,
            required=required,
            default=default,
        )

    @classmethod
    def response_invalid_operation(cls, operation: str) -> BaseToolResponse:
        return cls.response_error(message=f"Invalid operation: {operation}")

    @classmethod
    def response_missing_operation(cls) -> BaseToolResponse:
        return cls.response_error(message="Missing operation")

    @classmethod
    def response_parameter_missing(cls, operation: str, parameters: list[str]) -> BaseToolResponse:
        return cls.response_error(
            f"parameter(s) [{', '.join(parameters)}] required by operation `{operation}`"
        )

    @classmethod
    def get_operation_config(cls) -> Dict[str, Operation]:
        return {}

    @classmethod
    def get_tool_schema(cls) -> Dict[str, Any]:
        tool_name = cls.get_name_from_cls()
        docstring_text = cls.__doc__ or ""

        if DOCSTRING_PARSER_AVAILABLE and docstring_text:
            docstring = parse_docstring(docstring_text)
            description_parts = []
            if docstring.short_description:
                description_parts.append(docstring.short_description.strip())
            if docstring.long_description:
                description_parts.append(docstring.long_description.strip())
            description = "\n\n".join(description_parts)
        else:
            description = docstring_text.strip()

        operation_config: Dict[str, Operation] = cls.get_operation_config()
        properties: Dict[str, Any] = {
            "operation": {
                "type": "string",
                "enum": list(operation_config.keys()),
            }
        }
        operation_description_list: List[str] = []

        for operation in operation_config.values():
            parameter_list: List[str] = []
            for parameter in operation.parameters.values():
                if parameter.name not in properties:
                    properties[parameter.name] = {
                        "type": parameter.type,
                        "description": parameter.description,
                    }
                parameter_list.append(f"{parameter.name}:{parameter.type}")

            if parameter_list:
                operation_description_list.append(
                    f"- {operation.name}({', '.join(parameter_list)}): {operation.description}"
                )
            else:
                operation_description_list.append(f"- {operation.name}: {operation.description}")

        if operation_description_list:
            description += "\n\nOperations:\n" + "\n".join(operation_description_list)

        return {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["operation"],
            },
        }

    def operation(self, operation: str) -> Operation | None:
        return self.operations_and_parameters.get(operation)

    async def execute(self, **kwargs) -> BaseToolResponse:
        operation = kwargs.get("operation")
        if operation is None:
            return self.response_missing_operation()

        params = {k: v for k, v in kwargs.items() if k != "operation"}
        return self._execute(operation=str(operation), **params)

    def _execute(self, operation: str, **kwargs) -> BaseToolResponse:
        operation_obj = self.operation(operation)
        if operation_obj is None:
            return self.response_invalid_operation(operation)

        collected: dict = {}
        missing: list[str] = []
        for parameter in operation_obj.parameters.values():
            if parameter.name in kwargs:
                collected[parameter.name] = kwargs[parameter.name]
            elif parameter.required:
                missing.append(parameter.name)

        if missing:
            return self.response_parameter_missing(operation=operation, parameters=missing)

        try:
            result = operation_obj.command(**collected)
            return self.response_success(message=operation_obj.result_formatter(result))
        except Exception as e:
            return self.response_exception(e, f"Operation {operation} failed")
