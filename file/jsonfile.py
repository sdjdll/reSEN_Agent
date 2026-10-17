from typing import Any
from json import JSONDecoder,JSONEncoder,JSONDecodeError

from .filerw import filerw

class jsonfile:
    f: filerw
    def __init__(self, path: str):
        self.f = filerw(path)
    
    def read(self)->Any:
        """读出来就是 json 原样（可能是 dict、list……）；空文件/坏内容返回 {}。"""
        rawfile = self.f.read()
        j = JSONDecoder()
        try:
            return j.decode(rawfile)
        except JSONDecodeError as _:
            return {}
    
    def write(self,data: Any):
        # 缩进 4 + 不转义中文：这个文件是手改的，落盘后要还能看
        j = JSONEncoder(ensure_ascii=False, indent=4)
        self.f.write(j.encode(data) + "\n")