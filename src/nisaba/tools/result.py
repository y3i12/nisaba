from typing import Any, Dict, TYPE_CHECKING
from nisaba.tools.base_tool import BaseToolResponse
from nisaba.tools.base_operation_tool import BaseOperationTool, Operation
# from nisaba.wrapper.proxy import get_request_modifier

if TYPE_CHECKING:
    from nisaba.factory import MCPFactory

class ResultTool(BaseOperationTool):
    """Manage tool result in context.messages, allowing the results to be shown andhidden, saving context"""

    def __init__(self, factory:"MCPFactory"):
        super().__init__(
            factory=factory
        )

    @classmethod
    def nisaba(cls) -> bool:
        return True
    
    @classmethod
    def tool_result_response(cls, result:dict[str,Any]) -> str:
        return f"modified: {len(result['modified'])}"
    
    @classmethod
    def get_operation_config(cls) -> Dict[str,Operation]:
        return cls.make_operations([
                cls.make_operation(
                    command=get_request_modifier().show_tool_results,
                    name='show',
                    description='Show tool results',
                    result_formatter=cls.tool_result_response,
                    parameters=[
                        cls.make_parameter(name='tool_ids', required=True, type='array', description='List of `tool_use_id`')
                    ]
                ),
                cls.make_operation(
                    command=get_request_modifier().hide_tool_results,
                    name='hide',
                    description='Hide tool results',
                    result_formatter=cls.tool_result_response,
                    parameters=[
                        cls.make_parameter(name='tool_ids', required=True, type='array', description='List of `tool_use_id`')
                    ]
                ),
                cls.make_operation(
                    command=get_request_modifier().hide_all_tool_results,
                    name='collapse_all',
                    description='Hide ALL tool results',
                    result_formatter=cls.tool_result_response,
                    parameters=[],
                    skip_render=True
                )
            ])

    def _render(self):
        pass
