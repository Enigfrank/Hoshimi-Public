"""客户端材料兑换（cs_15022 -> sc_15023）与过期货币读取（cs_15024 -> sc_15025）。"""

from functools import lru_cache

from battle_rewards import grant_rewards, inventory_pushes, spend_items
from gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def item_exchange_catalog() -> dict[int, dict]:
    """按兑换 ID 索引 ItemExchangeCfg，取一次表后常驻。"""
    rows = read_client_data('''(function()
        local r = {}
        local c = require('ItemExchangeCfg')
        for _, id in ipairs(c.all or {}) do
            local v = c[id]
            if v then
                r[#r + 1] = {id = v.id, item_id = v.item_id, num = v.num or 0,
                    cost = v.cost or {}, activity_id = v.activity_id or 0}
            end
        end
        return r
    end)()''')
    return {row['id']: row for row in rows}


def exchange_items(user: dict, request: dict) -> dict:
    """按 ItemExchangeCfg 校验并执行材料兑换，返回 sc_15023 所需的字段。"""
    catalog = item_exchange_catalog()
    exchange_id = request.get('exchange_id')
    num = request.get('num')
    if exchange_id not in catalog:
        raise ValueError('兑换配置不存在')
    if type(num) is not int or num <= 0:
        raise ValueError('兑换数量必须大于零')
    row = catalog[exchange_id]
    costs = [{'id': cost[0], 'num': cost[1] * num} for cost in row['cost']]
    rewards = [{'id': row['item_id'], 'num': row['num'] * num}]
    # 先扣后发：任一项校验不过都不改动库存，与购买流程保持一致。
    spend_items(user, costs)
    grant_rewards(user, rewards)
    return {'result': 0, 'reward_list': rewards, 'inventory': inventory_pushes(user)}


def read_expired_currency(user: dict) -> dict:
    """读取已过期货币后清空客户端记录，服务端无过期货币状态可清。"""
    return {'result': 0}
