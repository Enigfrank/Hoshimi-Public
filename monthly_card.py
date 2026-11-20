"""恒定观测月卡：默认购买状态、30 天有效期与每日 90 移转之辉。"""

from functools import lru_cache
import time

from battle_rewards import grant_rewards
from gameplay_protocol import read_client_data
from server_clock import business_day

MONTHLY_CARD_COMMANDS = {34024}
MONTHLY_CARD_DURATION = 30 * 86400


@lru_cache(maxsize=1)
def monthly_card_catalog() -> dict:
    """读取月卡每日奖励，与客户端月卡页面使用同一配置。"""
    return read_client_data("""(function()
        local v=require('GameSetting').monthly_card_reward_daily.value
        return {item=v[1], amount=v[2]}
    end)()""")


def _state(user: dict) -> dict:
    """缺少状态时按默认已购买创建，已有状态保留购买时间和领取日。"""
    state = user.setdefault('monthly_card', {})
    now = int(time.time())
    if not state.get('timestamp'):
        state.update({'num': 1, 'timestamp': now + MONTHLY_CARD_DURATION, 'last_sign_day': 0})
    return state


def monthly_card_data(user: dict) -> dict:
    """下发月卡状态；默认已购买但保持未领取，由客户端页面手动领取。"""
    state = _state(user)
    now = int(time.time())
    active = int(state.get('timestamp', 0)) > now
    today = business_day(now)
    return {'monthly_card_num': int(state.get('num', 1)),
            'monthly_card_timestamp': int(state.get('timestamp', 0)),
            'is_sign': 1 if active and int(state.get('last_sign_day', 0)) == today else 0}


def monthly_card_request(user: dict) -> dict:
    """月卡页面手动领取入口；同一天只允许领取一次。"""
    state = _state(user)
    now = int(time.time())
    if int(state.get('timestamp', 0)) <= now:
        raise ValueError('月卡未激活或已过期')
    today = business_day(now)
    if int(state.get('last_sign_day', 0)) == today:
        raise ValueError('今日月卡奖励已领取')
    catalog = monthly_card_catalog()
    reward = [{'id': catalog['item'], 'num': catalog['amount']}]
    grant_rewards(user, reward)
    state['last_sign_day'] = today
    return {'result': 0, 'is_sign': 1, 'reward_list': reward}


def monthly_card_pushes(user: dict) -> list[tuple]:
    """登录时下发 34023，客户端据此恢复月卡天数和今日领取状态。"""
    return [(34023, monthly_card_data(user))]
