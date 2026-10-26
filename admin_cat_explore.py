"""弥弥观测站初始化、派遣、养成与领奖事务。"""

from copy import deepcopy
from functools import lru_cache
import time

from battle_rewards import grant_rewards, inventory_pushes, spend_items
from currency_data import build_currency_balances
from gameplay_protocol import read_client_data
from server_clock import business_day

EXPLORE_COMMANDS = {67002, 67004, 67006, 67008, 67010, 67012}


@lru_cache(maxsize=1)
def explore_catalog() -> dict:
    """读取当前客户端的等级、地区、弥弥与技能配置。"""
    return read_client_data('''(function()
        local r={}
        for _,name in ipairs({'ExploreLevelCfg','ExploreAreaCfg','ExploreMeowCfg',
                'ExploreMeowSkillCfg','ExploreMeowInbornCfg'}) do
            local c=require(name); local rows={}
            for _,id in ipairs(c.all) do rows[#rows+1]=c[id] end
            r[name]=rows
        end
        r.weekly_rates=require('GameSetting').explore_accumulation_multiplying_power.value
        return r
    end)()''')


def explore_level(user: dict) -> dict:
    """按累计观测经验计算等级，与原始客户端逐级扣经验一致。"""
    experience = build_currency_balances(user).get(53, 0)
    levels = explore_catalog()['ExploreLevelCfg']
    for level in levels:
        if not level['exp'] or experience < level['exp']:
            return level
        experience -= level['exp']
    return levels[-1]


def explore_state(user: dict) -> dict:
    """补齐有效初始状态，按北京时间业务日刷新每日与每周标记。"""
    state = user.setdefault('admin_cat_explore', {})
    default = {'mimir': [{'mimir_id': 10001, 'skill': [{'skill_id': 10001, 'skill_level': 1}]}],
               'explore_queue': [], 'weekly_time': 0, 'daily_time': 0,
               'weekly_reward_state': 0, 'weekly_scene_open': 0, 'total_explore_c': 0}
    for key, value in default.items():
        state.setdefault(key, deepcopy(value))
    day = business_day(int(time.time()))
    week = (day + 3) // 7
    if state.get('week', week) != week:
        state.update(weekly_time=0, weekly_reward_state=0, weekly_scene_open=0)
    if state.get('day', day) != day:
        state['daily_time'] = 0
    state.update(day=day, week=week)
    return state


def explore_data(user: dict) -> dict:
    """仅输出 67001 的协议字段，内部日期和领取事实留在存档。"""
    state = explore_state(user)
    return {key: deepcopy(state[key]) for key in ('mimir', 'explore_queue', 'weekly_time',
            'daily_time', 'weekly_reward_state', 'weekly_scene_open', 'total_explore_c')}


def _config(name: str, identifier: int) -> dict:
    """只接受当前配置存在的编号。"""
    row = next((row for row in explore_catalog()[name] if row['id'] == identifier), None)
    if row is None:
        raise ValueError('观测站配置编号无效')
    return row


def _costs(rows) -> list[dict]:
    """把客户端的成对物品配置转换为统一扣费格式。"""
    return [{'id': item, 'num': amount} for item, amount in rows]


def explore_request(user: dict, command: int, request: dict) -> tuple[dict, list[tuple]]:
    """校验观测站操作，返回回调及后置库存同步；外层编码成功后保存克隆账号。"""
    state, level = explore_state(user), explore_level(user)
    response = {'result': 0}
    if command == 67012:
        state['weekly_scene_open'] = 1
    elif command == 67010:
        cat = _config('ExploreMeowCfg', request['mimir_id'])
        if cat['id'] not in level['meow'] or any(
                amount > state['total_explore_c'] for _, amount in cat['unlock_condition']):
            raise ValueError('弥弥解锁条件未满足')
        if any(row['mimir_id'] == cat['id'] for row in state['mimir']):
            raise ValueError('弥弥已解锁')
        spend_items(user, _costs(cat['consume']))
        state['mimir'].append({'mimir_id': cat['id'],
                               'skill': [{'skill_id': cat['skill'], 'skill_level': 1}]})
    elif command == 67002:
        cat = _config('ExploreMeowCfg', request['mimir_id'])
        owned = next((row for row in state['mimir'] if row['mimir_id'] == cat['id']), None)
        if owned is None or request['skill_id'] != cat['skill']:
            raise ValueError('弥弥或技能无效')
        if any(row['mimir_id'] == cat['id'] for row in state['explore_queue']):
            raise ValueError('派遣中的弥弥不能升级')
        skill = _config('ExploreMeowSkillCfg', cat['skill'])
        current = owned['skill'][0]['skill_level']
        if current >= len(skill['skill_effect']):
            raise ValueError('技能已满级')
        spend_items(user, _costs(skill['skill_up_consume'][current - 1]))
        owned['skill'][0]['skill_level'] += 1
    elif command == 67004:
        area = _config('ExploreAreaCfg', request['area_id'])
        cat, hours = request['mimir_id'], request['hour_time']
        if area['id'] not in level['area'] or hours not in (4, 8, 10, 12, 20) or hours > level['time']:
            raise ValueError('地区或派遣时长尚未解锁')
        if not any(row['mimir_id'] == cat for row in state['mimir']):
            raise ValueError('弥弥尚未解锁')
        queue = state['explore_queue']
        if len(queue) >= level['amount'] or any(
                row['area_id'] == area['id'] or row['mimir_id'] == cat for row in queue):
            raise ValueError('地区、弥弥或派遣队列已被占用')
        now = int(time.time())
        queue.append({'area_id': area['id'], 'mimir_id': cat, 'start_time': now,
                      'stop_time': now + hours * 3600, 'target_explore_hour': hours, 'explore_event': []})
    elif command == 67006:
        queue = state['explore_queue']
        dispatched = next((row for row in queue if row['area_id'] == request['area_id']), None)
        if dispatched is None or dispatched['stop_time'] > int(time.time()):
            raise ValueError('该地区没有已完成的派遣')
        area = _config('ExploreAreaCfg', dispatched['area_id'])
        cat = _config('ExploreMeowCfg', dispatched['mimir_id'])
        inborn = _config('ExploreMeowInbornCfg', cat['inborn'])['inborn_effect']
        skill = _config('ExploreMeowSkillCfg', cat['skill'])
        owned = next(row for row in state['mimir'] if row['mimir_id'] == cat['id'])
        hours = dispatched['target_explore_hour']
        base_rate = inborn[1] if area['id'] == inborn[0] else 0
        skill_type = skill['skill_type']
        applies = skill_type == 5 or skill_type in (1, 2) and hours <= 8 or skill_type in (3, 4) and hours > 8
        bonus = skill['skill_effect'][owned['skill'][0]['skill_level'] - 1] if applies else 0
        rewards = []
        for item, amount in area['reward']:
            extra = bonus if (item == 53 and skill_type in (2, 4, 5)
                              or item != 53 and skill_type in (1, 3, 5)) else 0
            rewards.append({'id': item, 'num': amount * hours * (100 + base_rate + extra) // 100})
        grant_rewards(user, rewards)
        state['total_explore_c'] += sum(row['num'] for row in rewards if row['id'] == 56)
        if not state['daily_time'] and not state['weekly_reward_state']:
            state['weekly_time'] += 1
        state['daily_time'] = 1
        queue.remove(dispatched)
        response.update(event_id=0, reward_list=rewards)
    elif command == 67008:
        day = business_day(int(time.time()))
        weekday = (day + 3) % 7
        if weekday < 5 or not state['weekly_time'] or state['weekly_reward_state']:
            raise ValueError('本周累计奖励不可领取')
        rates = explore_catalog()['weekly_rates']
        rate = rates[min(state['weekly_time'], len(rates)) - 1]
        rewards = [{'id': item, 'num': amount * rate} for item, amount in level['accumulate_rewards']]
        grant_rewards(user, rewards)
        state.update(weekly_time=0, weekly_reward_state=1)
        response['reward_list'] = rewards
    else:
        raise ValueError('观测站请求未实现')
    # 派遣开始前置同步；其余操作在回调后同步，既保留结算所需旧队列，也刷新累计天数。
    pushes = inventory_pushes(user)
    pushes.append((67001, explore_data(user)))
    return response, pushes
