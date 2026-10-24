from typing import cast,Any

from .jsonfile import jsonfile

class dick:
    jf:jsonfile
    __data:dict
    def __init__(self,path:str):
        self.jf = jsonfile(path)
        self.__data = self.jf.read()

    def int(self,key:str,default:int=0)->int:
        return cast(int, self.any(key,default))

    def str(self,key:str,default:str="")->str:
        return cast(str, self.any(key,default))

    def bool(self,key:str,default:bool=False)->bool:
        return cast(bool, self.any(key,default))

    def float(self,key:str,default:float=0.0)->float:
        return cast(float, self.any(key,default))

    def any(self,key:str,default:Any)->Any:
        if key not in self.__data:
            self.__data.setdefault(key,default)
        return self.__data.get(key, default)

    def items(self)->dict[str,Any]:
        return dict(self.__data)

    def set(self,key:str,value:Any)->None:
        self.__data[key] = value

    def pop(self,key:str)->None:
        self.__data.pop(key, None)

    def update(self)->None:
        self.jf.write(self.__data)