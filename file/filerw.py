import os
import tempfile

class filerw:
    path: str
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        try:
            with open(path, mode="x") as f:
                f.write("")
        except FileExistsError:
            pass

    def read(self) -> str:
        raw: str
        with open(file=self.path, mode="r", encoding='utf-8') as f:
            raw = f.read()
        return raw.strip()
    
    def write(self, raw: str):
        directory = os.path.dirname(os.path.abspath(self.path))
        prefix = f".{os.path.basename(self.path)}."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix)
        try:
            with os.fdopen(fd, "w", encoding='utf-8') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise