from typing import Any
from json import JSONDecoder,JSONEncoder,JSONDecodeError

from .filerw import filerw

class jsonfile:
    _f: filerw
    _ascii: bool
    def __init__(self, path: str, ascii: bool = False):
        self.f = filerw(path)
        self._ascii = ascii
    
    def read(self)->Any:
        """读出来就是 json 原样（可能是 dict、list……）；空文件/坏内容返回 {}。"""
        rawfile = self.f.read()
        j = JSONDecoder()
        try:
            return j.decode(rawfile)
        except JSONDecodeError as _:
            return {}
    
    def write(self,data: Any,indent: int|None=4):
        if indent is None:
            j = JSONEncoder(ensure_ascii=self._ascii, separators=(",",":"))
        else:
            j = JSONEncoder(ensure_ascii=self._ascii, indent=indent)
        self.f.write(j.encode(data) + "\n")