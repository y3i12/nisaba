from typing import Dict, Any, TYPE_CHECKING
from nisaba.tools.base_operation_tool import BaseOperationTool, Operation
from nisaba.tools.base_tool import BaseToolResponse
from nisaba.augments import get_augment_manager
from nisaba import session_context

if TYPE_CHECKING:
    from nisaba.factory import MCPFactory

class AugmentTool(BaseOperationTool):
    """
    Operations load, unload, (un)pin, and learn augments. 
    
    Augments live in the system prompt and they mutate how the entire context is interpreted. Think of
    augments as 'dynamic context libraries', which can contain theoretical knowledge, practical knowledge,
    memories, documentation, procedures, references, mindsets... information.
    """    
    def __init__(self, factory:"MCPFactory"):
        super().__init__(
            factory=factory
        )

    @classmethod
    def nisaba(cls) -> bool:
        return True
    
    @classmethod
    def response_augment_manager_not_present(cls) -> BaseToolResponse:
        return cls.response(success=False, message="ConfigurationError: Augments system not initialized")
    
    @classmethod
    def augment_manager_result_response(cls, result:dict[str,Any]) -> str:
        message_list:list[str] = []
        for key in ('affected', 'dependencies', 'skipped'):
            message_list = cls._augment_result_append_key(result, key, message_list)

        message = ', '.join(message_list)
        return message
    
    @classmethod
    def _augment_result_append_key(cls, result:dict[str,Any], key:str, message_list:list[str]) -> list[str]:
        if key in result:
            message_list.append(f"{key} [{', '.join(result[key])}]")
        return message_list

    @classmethod
    def _get_session_augment_manager(cls):
        """Get session-specific augment manager."""
        session_id = session_context.get_current_session()
        if not session_id:
            raise RuntimeError("No active session - cannot access augments")
        return get_augment_manager(session_id)

    @classmethod
    def get_operation_config(cls) -> Dict[str,Operation]:
        return cls.make_operations([
                cls.make_operation(
                    command=lambda **kwargs: cls._get_session_augment_manager().activate_augments(**kwargs),
                    name='load',
                    description='Load augments matching patterns',
                    result_formatter=cls.augment_manager_result_response,
                    parameters=[
                        cls.make_parameter(name='patterns', required=True, type='array', description='List of patterns to match')
                    ]
                ),
                cls.make_operation(
                    command=lambda **kwargs: cls._get_session_augment_manager().deactivate_augments(**kwargs),
                    name='unload',
                    description='Unload augments matching patterns',
                    result_formatter=cls.augment_manager_result_response,
                    parameters=[
                        cls.make_parameter(name='patterns', required=True, type='array', description='List of patterns to match')
                    ]
                ),
                cls.make_operation(
                    command=lambda **kwargs: cls._get_session_augment_manager().pin_augment(**kwargs),
                    name='pin',
                    description='Pin augments matching patterns',
                    result_formatter=cls.augment_manager_result_response,
                    parameters=[
                        cls.make_parameter(name='patterns', required=True, type='array', description='List of patterns to match')
                    ],
                    skip_render=True
                ),
                cls.make_operation(
                    command=lambda **kwargs: cls._get_session_augment_manager().unpin_augment(**kwargs),
                    name='unpin',
                    description='Unpin augments matching patterns',
                    result_formatter=cls.augment_manager_result_response,
                    parameters=[
                        cls.make_parameter(name='patterns', required=True, type='array', description='List of patterns to match')
                    ],
                    skip_render=True
                ),
                cls.make_operation(
                    command=lambda **kwargs: cls._get_session_augment_manager().learn_augment(**kwargs),
                    name='store',
                    description='Store augment in group/name',
                    result_formatter=cls.augment_manager_result_response,
                    parameters=[
                        cls.make_parameter(name='group',   required=True, type='string', description= 'Augment group/category (e.g., "code_analysis")'),
                        cls.make_parameter(name='name',    required=True, type='string', description= 'Augment name (e.g., "find_circular_deps")'),
                        cls.make_parameter(name='content', required=True, type='string', description= 'Augment content in markdown format'),
                    ],
                    skip_render=True
                )
            ])

    def _render(self):
        pass