from typing import cast,Any

from .jsonfile import jsonfile

class dick:
    jf:jsonfile
    __data:dict
    def __init__(self,path:str):
        self.jf = jsonfile(path)
        self.__data = self.jf.read()

    def int(self,key:str,default:int=0)->int:
        return cast(int, self.__data.get(key,default))

    def str(self,key:str,default:str="")->str:
        return cast(str, self.__data.get(key,default))

    def bool(self,key:str,default:bool=False)->bool:
        return cast(bool, self.__data.get(key,default))

    def float(self,key:str,default:float=0.0)->float:
        return cast(float, self.__data.get(key,default))

    def any(self,key:str,default:Any)->Any:
        return self.__data.get(key, default)

    def set(self,key:str,value:Any)->None:
        self.__data[key] = value

    def pop(self,key:str)->None:
        self.__data.pop(key, None)

    def update(self)->None:
        self.jf.write(self.__data)