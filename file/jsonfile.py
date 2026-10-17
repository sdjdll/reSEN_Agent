from typing import Any
from json import JSONDecoder,JSONEncoder,JSONDecodeError

from .filerw import filerw

class jsonfile:
    f: filerw
    def __init__(self, path: str):
        self.f = filerw(path)

    def read(self)->Any:
        rawfile = self.f.read()
        j = JSONDecoder()
        try:
            return j.decode(rawfile)
        except JSONDecodeError as _:
            return {}

    def write(self,data: Any):
        j = JSONEncoder(ensure_ascii=False, indent=4)
        self.f.write(j.encode(data) + "\n")