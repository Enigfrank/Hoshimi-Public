"""常驻玩法配置查询；只读取本机客户端，不携带官方配置数据。"""

from functools import lru_cache

from gameplay_protocol import read_client_data


@lru_cache(maxsize=64)
def resident_rows(name: str) -> list[dict]:
    """把固定配置表转成行列表，保留客户端的排序。"""
    if not name.isidentifier():
        raise ValueError('配置表名称无效')
    return read_client_data(f"(function() local c=require('{name}'); local r=setmetatable({{}},{{json_array=true}}); "
                            "for _,id in ipairs(c.all) do r[#r+1]=c[id] end return r end)()")


def resident_config(name: str, item: int, key: str = 'id') -> dict:
    """按真实配置校验请求编号，避免保存不存在的关卡或奖励。"""
    row = next((r for r in resident_rows(name) if r[key] == item), None)
    if row is None:
        raise ValueError(f'常驻玩法配置不存在：{name}/{item}')
    return row


def append_unique(values: list, item) -> bool:
    """追加一次进度标记，返回是否为首次完成。"""
    if item in values:
        return False
    values.append(item)
    return True
