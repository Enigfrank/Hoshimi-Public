"""百宝囊（大月卡）购买、每日随机奖励与累计奖励。"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import random
import time

from battle_rewards import grant_rewards, spend_items
from currency_data import build_currency_balances
from gameplay_protocol import read_client_data

BIG_MONTH_CARD_COMMANDS = {34110, 34114, 34116, 34118}


@lru_cache(maxsize=1)
def card_catalog() -> dict:
    """读取当前大月卡的持续时间、价格、每日奖励池与累计奖励。"""
    return read_client_data('''(function()
        local s=require('GameSetting'); local a=require('BigMonthCardAccumulationCfg')
        local r={template_id=s.big_monthly_card_accumulation.value[1],
                 duration=s.big_monthly_card_duration.value[1],
                 cost=s.big_monthly_card_cost.value,
                 daily=s.big_monthly_card_reward_daily.value,
                 refresh_hour=s.refresh_time1.value[1][1], accumulation={}}
        for _,id in ipairs(a.all) do local v=a[id]
            r.accumulation[#r.accumulation+1]={id=id,template=v.template_id,
                accumulation=v.accumulation,reward=v.reward}
        end
        return r
    end)()''')


def _business_day(timestamp: int) -> str:
    """按客户端配置的北京时间刷新小时划分业务日。"""
    moment = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))) - timedelta(hours=card_catalog()['refresh_hour'])
    return moment.date().isoformat()


def _dead_timestamp(buy_timestamp: int) -> int:
    """与客户端一致：购买日刷新点后持续 duration+1 个自然日。"""
    catalog = card_catalog()
    buy = datetime.fromtimestamp(buy_timestamp, timezone(timedelta(hours=8)))
    base = buy.replace(hour=catalog['refresh_hour'], minute=0, second=0, microsecond=0)
    if buy.hour < catalog['refresh_hour']:
        base -= timedelta(days=1)
    return int((base + timedelta(days=catalog['duration'] + 1)).timestamp())


def _active(state: dict, now: int) -> bool:
    """仅购买后且未到期的百宝囊可领取每日奖励。"""
    buy = int(state.get('buy_timestamp', 0) or 0)
    return buy > 0 and now < _dead_timestamp(buy)


def _state(user: dict) -> dict:
    """按需创建百宝囊存档，兼容尚未购买的旧账号。"""
    return user.setdefault('big_month_card', {})


def _drawn(state: dict) -> dict:
    """恢复奖励池各档已抽取次数。"""
    return {int(row['index']): int(row['times']) for row in state.get('daily_record', [])}


def big_month_card_data(user: dict) -> dict:
    """下发大月卡状态；未购买时也保留有效模板避免客户端空索引。"""
    catalog = card_catalog()
    state = user.get('big_month_card', {})
    now = int(time.time())
    buy = int(state.get('buy_timestamp', 0) or 0)
    active = _active(state, now)
    expired = buy > 0 and not active
    claimed = [int(value) for value in state.get('total_sign_receive_list', [])]
    return {'buy_timestamp': buy,
            'is_sign': 1 if active and state.get('last_sign_day') == _business_day(now) else 0,
            'total_sign_times': int(state.get('total_sign_times', 0)),
            'total_sign_receive_list': list(reversed(claimed)),
            'daily_record': list(state.get('daily_record', [])),
            'is_expire_tip': 1 if expired and not state.get('expire_ack') else 0,
            'template_id': int(state.get('template_id') or catalog['template_id'] or 1)}


def buy_big_month_card(user: dict) -> dict:
    """按客户端合并余额扣除移转之花并激活，过期前拒绝重复购买。"""
    catalog = card_catalog()
    state = _state(user)
    now = int(time.time())
    if _active(state, now):
        raise ValueError('百宝囊已激活')
    currency, price = map(int, catalog['cost'])
    costs = [{'id': currency, 'num': price}]
    if currency == 32:
        balances = build_currency_balances(user)
        costs, remaining = [], price
        for item in (32, 31, 30):
            amount = min(remaining, max(0, balances[item]))
            if amount:
                costs.append({'id': item, 'num': amount})
                remaining -= amount
        if remaining:
            raise ValueError('移转之花不足')
    spend_items(user, costs)
    state.clear()
    state.update({'buy_timestamp': now, 'template_id': int(catalog['template_id'] or 1),
                  'total_sign_times': 0, 'total_sign_receive_list': [],
                  'daily_record': [], 'last_sign_day': '', 'expire_ack': False})
    return {'result': 0}


def sign_today(user: dict) -> dict:
    """每日从剩余奖励池抽一份，并在达到累计档位时同时发放固定奖励。"""
    catalog = card_catalog()
    state = _state(user)
    now = int(time.time())
    if not _active(state, now):
        raise ValueError('百宝囊未激活或已过期')
    today = _business_day(now)
    if state.get('last_sign_day') == today:
        raise ValueError('今日已领取')
    daily = catalog['daily']
    drawn = _drawn(state)
    pool = []
    for index, row in enumerate(daily, 1):
        pool.extend([index] * max(0, int(row[2]) - drawn.get(index, 0)))
    if not pool:
        raise ValueError('每日奖励已领完')
    index = random.choice(pool)
    rewards = [{'id': int(daily[index - 1][0]), 'num': int(daily[index - 1][1])}]
    drawn[index] = drawn.get(index, 0) + 1
    state['daily_record'] = [{'index': value, 'times': drawn[value]} for value in sorted(drawn) if drawn[value]]
    state['total_sign_times'] = int(state.get('total_sign_times', 0)) + 1
    claimed = set(int(value) for value in state.get('total_sign_receive_list', []))
    template = int(state.get('template_id') or catalog['template_id'] or 1)
    for entry in catalog['accumulation']:
        if int(entry['template']) == template and int(entry['accumulation']) <= state['total_sign_times'] \
                and int(entry['id']) not in claimed:
            rewards.append({'id': int(entry['reward'][0]), 'num': int(entry['reward'][1])})
            state.setdefault('total_sign_receive_list', []).append(int(entry['id']))
            claimed.add(int(entry['id']))
    state['last_sign_day'] = today
    grant_rewards(user, rewards)
    return {'result': 0, 'rewards': rewards, 'daily_record': state['daily_record'],
            'is_sign': 1, 'total_sign_times': state['total_sign_times'],
            'total_sign_receive_list': list(reversed(state.get('total_sign_receive_list', [])))}


def claim_accumulation(user: dict, ids: list) -> dict:
    """补领累计奖励；先校验整批条件，避免部分发放。"""
    catalog = card_catalog()
    state = _state(user)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('累计奖励列表无效')
    template = int(state.get('template_id') or catalog['template_id'] or 1)
    entries = {int(entry['id']): entry for entry in catalog['accumulation']
               if int(entry['template']) == template}
    claimed = set(int(value) for value in state.get('total_sign_receive_list', []))
    for value in ids:
        entry = entries.get(int(value))
        if entry is None or int(entry['accumulation']) > int(state.get('total_sign_times', 0)) \
                or int(value) in claimed:
            raise ValueError('累计奖励条件不足或已领取')
    rewards = [{'id': int(entries[int(value)]['reward'][0]), 'num': int(entries[int(value)]['reward'][1])}
               for value in ids]
    grant_rewards(user, rewards)
    state['total_sign_receive_list'] = sorted(claimed | {int(value) for value in ids})
    return {'result': 0, 'receive_time_limit_recharge_list': [], 'reward_list': rewards}


def clear_expire_tip(user: dict) -> dict:
    """客户端确认过期提示后清除红点。"""
    _state(user)['expire_ack'] = True
    return {'result': 0}


def big_month_card_request(user: dict, command: int, request: dict) -> dict:
    """处理购买、每日领取、过期确认与累计补领。"""
    if command == 34114:
        return buy_big_month_card(user)
    if command == 34110:
        return sign_today(user)
    if command == 34116:
        return clear_expire_tip(user)
    if command == 34118:
        return claim_accumulation(user, request.get('id_list', []))
    raise ValueError('百宝囊指令未支持')


def big_month_card_pushes(user: dict) -> list[tuple]:
    """登录时补发 34101，避免百宝囊页面读取空模板导致黑屏。"""
    return [(34101, big_month_card_data(user))]
