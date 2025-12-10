import json

from abc import abstractmethod
from dataclasses import dataclass
from nisaba.tools.base_tool import BaseTool, BaseToolResponse
from typing import Any, Callable, Dict, List, TYPE_CHECKING, get_type_hints

try:
    from docstring_parser import parse as parse_docstring
    DOCSTRING_PARSER_AVAILABLE = True
except ImportError:
    DOCSTRING_PARSER_AVAILABLE = False

if TYPE_CHECKING:
    from nisaba.factory import MCPFactory

@dataclass(unsafe_hash=True)
class OperationParameter:
    name:str
    required:bool
    type:str
    required_or:str|None
    default:Any|None
    description:str


@dataclass(unsafe_hash=True)
class Operation:
    command:Callable
    result_formatter:Callable
    name:str
    parameters:dict[str,OperationParameter]
    description:str
    skip_render:bool=False
  
class BaseOperationTool(BaseTool):
    def __init__(self, factory:"MCPFactory"):
        super().__init__(factory)
        self.operations_and_parameters:dict[str,Operation] = self.get_operation_config()
    
    @classmethod
    def make_operations(cls, operations:list[Operation]) -> dict[str, Operation]:
        return dict(map(lambda operation: (operation.name, operation), operations))

    @classmethod  
    def make_operation(cls, command:Callable, result_formatter:Callable, name:str, parameters:list[OperationParameter], description:str, skip_render:bool = False) -> Operation:
        return Operation(command=command, result_formatter=result_formatter, name=name, parameters=dict(map(lambda parameter: (parameter.name, parameter), parameters)), description=description, skip_render=skip_render)
    
    @classmethod
    def make_parameter(cls, name:str, type:str, description:str, default:Any|None = None, required:bool = False, required_or:str|None = None ) -> OperationParameter:
        return OperationParameter(name=name, required=required or isinstance(required_or, str), type=type, required_or=required_or, default=default, description=description)
    
    @classmethod
    def response_invalid_operation(cls, operation:str) -> BaseToolResponse:
        return cls.response_error(message=f"Invalid operation: {operation}")
    
    @classmethod
    def response_missing_operation(cls) -> BaseToolResponse:
        return cls.response_error(message=f"Missing operation")
    
    @classmethod
    def response_parameter_missing(cls, operation:str, parameters:list[str]) -> BaseToolResponse:
        return cls.response_error(f"parameter(s) [{', '.join(parameters)}] required by operation `{operation}`")
 
    @classmethod
    def _format_str(cls, _str:str) -> str:
        return f"{_str}"
    
    @classmethod
    def _format_ok(cls, ok:bool) -> str:
        if ok:
            return "ok"
        
        return "not ok and shouldn't happen"
    
    @classmethod
    def get_operation_config(cls) -> Dict[str,Operation]:
        """
        Needs override
        """
        return {}

    @classmethod
    def get_tool_schema(cls) -> Dict[str, Any]:
        """
        Generate JSON schema from execute() signature and docstring.

        Returns:
            Dict containing tool name, description, and parameter schema
        """
        tool_name = cls.get_name_from_cls()

        # Parse docstring
        docstring_text = cls.__doc__ or ""

        if DOCSTRING_PARSER_AVAILABLE and docstring_text:
            docstring = parse_docstring(docstring_text)

            # Build description
            description_parts = []
            if docstring.short_description:
                description_parts.append(docstring.short_description.strip())
            if docstring.long_description:
                description_parts.append(docstring.long_description.strip())

            description = "\n\n".join(description_parts)
        else:
            description = docstring_text.strip()

        # Build parameter schema
        properties = {}
        operation_config:Dict[str, Operation] = cls.get_operation_config()
        properties['operation'] = {
            'type': 'string',
            'enum': list(operation_config.keys())
        }
        operation_description_list:List[str] = []

        for operation in operation_config.values():
            parameter_list:List[str] = []
            visited_params = set()

            # Build parameter list and add to schema properties
            # Handle OR-chains by grouping them together
            for parameter_name in operation.parameters.keys():
                if parameter_name in visited_params:
                    continue

                parameter:OperationParameter = operation.parameters[parameter_name]
                if parameter.name not in properties:
                    properties[parameter.name] = {'type':parameter.type, 'description':parameter.description}

                # Check if this parameter is part of an OR-chain
                if parameter.required and parameter.required_or is not None:
                    or_chain = [f"{parameter.name}:{parameter.type}"]
                    visited_params.add(parameter.name)
                    current = parameter
                    # Follow the chain
                    while current.required_or is not None:
                        current = operation.parameters[current.required_or]
                        or_chain.append(f"{current.name}:{current.type}")
                        visited_params.add(current.name)
                    parameter_list.append(f"({' OR '.join(or_chain)})")
                else:
                    visited_params.add(parameter.name)
                    parameter_list.append(f"{parameter.name}:{parameter.type}")

            operation_description = ""
            if len(parameter_list):
                operation_description = f"- {operation.name}({', '.join(parameter_list)}): {operation.description}"
            else:
                operation_description = f"- {operation.name}: {operation.description}"

            operation_description_list.append(operation_description)
        
        if len(operation_description_list):
            description += "\n\nOperations:\n" + "\n".join(operation_description_list)

        return {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ['operation']
            }
        }
    
    def operation(self, operation:str) -> Operation|None:
        return self.operations_and_parameters.get(operation)
   
    async def execute(self, **kwargs) -> BaseToolResponse:
        operation = kwargs.get('operation', None)
        if operation is None:
            return self.response_missing_operation()

        # Remove 'operation' from kwargs to avoid duplicate argument error
        params = {k: v for k, v in kwargs.items() if k != 'operation'}
        return self._execute(operation=str(operation), **params)
    
    def _execute(self, operation:str, **kwargs) -> BaseToolResponse:
        """
        Execute the operation tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            Dict with success/error response
        """
        operation_obj = self.operation(operation)

        if operation_obj is None:
            return self.response_invalid_operation(operation)
        
        collected_parameters = {}
        missing_parameters = []
        parameters_to_visit = list(operation_obj.parameters.keys())

        while len(parameters_to_visit):
            parameter = operation_obj.parameters[parameters_to_visit.pop(0)]

            # handles or chain, needs to be sequential
            # TODO: error handling would be nice, but it is luxury
            if parameter.required and parameter.required_or is not None:
                processing_parameter_chain = True
                selected_parameter:OperationParameter|None = None
                or_chain_names = []
                while processing_parameter_chain:
                    or_chain_names.append(parameter.name)

                    if parameter.name in kwargs:
                        if selected_parameter is None:
                            selected_parameter = parameter
                            collected_parameters[parameter.name] = kwargs[parameter.name]
                                                    
                    if parameter.required_or is None:
                        # end of list
                        processing_parameter_chain = False

                        if parameter.required and selected_parameter is None:
                            missing_parameters.append(' OR '.join(or_chain_names))

                    if processing_parameter_chain:
                        parameter = operation_obj.parameters[parameters_to_visit.pop(0)]

            elif parameter.required and parameter.name not in kwargs:
                missing_parameters.append(parameter.name)

            elif parameter.name in kwargs:
                collected_parameters[parameter.name] = kwargs[parameter.name]
        
        if len(missing_parameters):
            return self.response_parameter_missing(operation=operation, parameters=missing_parameters)

        try:
            result = operation_obj.command(**collected_parameters)

            if not operation_obj.skip_render:
                self._render()
            
            return self.response_success(message=operation_obj.result_formatter(result))
        
        except Exception as e:
            return self.response_exception(e, f"Operation {operation} failed")

    @abstractmethod
    def _render(self) -> None:
        pass