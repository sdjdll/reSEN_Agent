from abc import ABC, abstractmethod

class hotplug_tool(ABC):
    @abstractmethod
    def run(self, tool_name:str, *args):
        raise NotImplementedError("The 'run' method must be implemented by subclasses of hotplug_tool.")
    @abstractmethod
    def get_schema(self) -> dict:
        raise NotImplementedError("The 'get_schema' method must be implemented by subclasses of hotplug_tool.")