"""对策协议等级、领奖记录与每周经验的持久化。"""

from functools import lru_cache

from battle_rewards import grant_rewards, spend_items
from currency_data import build_currency_balances
from gameplay_protocol import read_client_data


def passport_rewards(user: dict) -> list[dict]:
    """保留有效领奖记录，恢复数字字符串并过滤无法编码的旧占位数据。"""
    received = user.get('passport_rewards', [])
    if not isinstance(received, list):
        return []
    rewards = []
    for record in received:
        if not isinstance(record, dict):
            continue
        fields = []
        for name in ('id', 'is_pay'):
            value = record.get(name)
            if isinstance(value, str) and value.isascii() and value.isdecimal():
                value = int(value)
            if type(value) is not int or not 0 <= value <= 0xffffffff:
                break
            fields.append(value)
        else:
            rewards.append({'id': fields[0], 'is_pay': fields[1]})
    return rewards


@lru_cache(maxsize=1)
def passport_catalog() -> dict:
    """读取本机主题的奖励、等级经验和购买价格。"""
    from gameplay_data import client_catalog
    return read_client_data(f'''(function()
        local list=require('BattlePassListCfg')[{client_catalog()['passport_id']}]
        local cfg=require('BattlePassCfg'); local r={{rewards={{}},exp={{}}}}
        for level,id in ipairs(cfg.get_id_list_by_type[list.battlepass_type]) do
            r.rewards[#r.rewards+1]=cfg[id]
            r.exp[#r.exp+1]=require('GameLevelSetting')[level].battlepass_lv_exp_sum
        end
        r.price=require('GameSetting').battlepass_level_price.value
        return r
    end)()''')


def passport_level(user: dict) -> int:
    """按客户端累计经验表计算当前等级。"""
    exp = build_currency_balances(user)[14]
    return sum(exp >= threshold for threshold in passport_catalog()['exp'])


def passport_request(user: dict, command: int, request: dict) -> dict:
    """校验等级与付费档位后领奖，或按配置扣款购买等级。"""
    catalog = passport_catalog()
    level = passport_level(user)
    if command == 34036:
        count = request['num']
        if count <= 0 or level + count > len(catalog['exp']):
            raise ValueError('对策协议已满级或购买数量无效')
        currency, price = catalog['price']
        spend_items(user, [{'id': currency, 'num': price * count}])
        current = build_currency_balances(user)[14]
        weekly = user.get('passport_weekly_exp', 0)
        grant_rewards(user, [{'id': 14, 'num': catalog['exp'][level+count-1] - current}])
        user['passport_weekly_exp'] = weekly
        return {'result': 0}
    received = passport_rewards(user)
    claimed = {(r['id'], r['is_pay']) for r in received}
    tiers = (0, 1) if user.get('passport_pay_level', 0) else (0,)
    eligible = {(cfg['id'], tier): cfg['reward_pay' if tier else 'reward_free']
                for cfg in catalog['rewards'][:level] for tier in tiers
                if cfg['reward_pay' if tier else 'reward_free']}
    if command == 34032:
        key = (request['id'], request['is_pay'])
        if key not in eligible or key in claimed:
            raise ValueError('奖励未解锁或已经领取')
        keys = [key]
    else:
        keys = [(cfg['id'], tier) for cfg in catalog['rewards'][:level] for tier in tiers
                if (cfg['id'], tier) in eligible and (cfg['id'], tier) not in claimed]
        if not keys:
            raise ValueError('没有可领取奖励')
    rewards = [{'id': item, 'num': amount} for key in keys for item, amount in eligible[key]]
    grant_rewards(user, rewards)
    received.extend({'id': key[0], 'is_pay': key[1]} for key in keys)
    user['passport_rewards'] = received
    return {'result': 0, 'receive_info': received, 'reward_list': rewards}
