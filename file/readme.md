# file
> file包旨在简化/规范python的文件读写，将所需的操作包装为一个函数，实现一句话实现一个复杂操作

## [filerw.py](filerw.py)
> file包的读写核心，用于对文件进行读写
- `filerw`保证了原子读写，所以基于`filerw`的模块都是原子的
### read()
- read用于读self.path路径下的文件
    - 完整读，将整个文件加载进内存
- UTF-8编码
### write(raw: str)
- write用于写self.path路径下的文件
    - 完整写，直接覆盖整个文件
- UTF-8编码

可以看到rw操作都是全量的，在某些时刻操作可能非常重

## [jsonfile.py](jsonfile.py)
> `filerw`的一个实现，将具体的`rawfile`抽象为了`jsonfile`，并将`jsonfile`转化为`dict`

### read()
- read()用于读取文件，将文件读进内存，并将`str`转化为`dict`
    - 静默报错，当解码错误时降级为`{}`
### write(data: dict)
- write()用于写文件，将内存中的`dict`转化为`str`，并写入文件

## [dick.py](dick.py)
> 利用`jsonfile.py`实现了一个可方便拓展的配置读写
### 读：
- 提供了`int` `str` `bool` `float`方法,将读取到值以该类型返回
- 提供了 `any` 方法，用于将不规则的值以 `Any` 返回
- 提供了 `default` 参数，当未读取到值时返回 `default`

### 写：
- set(key: str, value: Any)
    - 设置 `key` 的值为 `value`，支持任意类型
- update()
    - 更新文件，内存中的`dict`全量写入文件

### 特性
- 轻量读，重量写
    - 除了初始化以外，所有的读操作都在内存中，速度极快
- 一般情况下，内存的数据永远比磁盘里的新
    - `set`只更新内存，避免了磁盘大量写入
- 多实例状态下后写入的数据会覆盖前面的，所以最好全局单实例，或者一个文件一个实例

## [config.py](config.py)
> 全局共享的 `configure/config.json` 句柄
- 模块级单例 `config`：整个进程只构造一次 `dick`，避免多实例互相覆写
- `config.json` 不存在时先写入 `DEFAULT_CONFIG`；`config_created` 标记是否为本次新建
- 其它模块统一 `from file.config import config`，不要再自己 `dick("…/config.json")`
