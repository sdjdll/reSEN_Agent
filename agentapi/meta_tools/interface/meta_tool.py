from abc import ABC, abstractmethod
class meta_tool(ABC):
    schema:list[dict] = []
    @abstractmethod
    def run(self, *args, **kwargs):
        raise NotImplementedError("The 'run' method must be implemented by subclasses of meta_tool.")
