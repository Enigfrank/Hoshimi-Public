"""游园街每日、每周和偶像关卡任务的真实进度。"""

import time

from backhome_data import backhome_state
from server_clock import business_day


def home_event(user: dict, condition: int, amount: int = 1) -> None:
    """只由成功业务事件累计日／周统计，查询和重复结算不会增加次数。"""
    state = backhome_state(user)
    day = business_day(int(time.time()))
    for name, period in (('daily_events', day), ('weekly_events', (day + 3) // 7)):
        stats = state.setdefault(name, {'period': period, 'counts': {}})
        if stats['period'] != period:
            stats['period'], stats['counts'] = period, {}
        key = str(condition)
        stats['counts'][key] = stats['counts'].get(key, 0) + amount


def update_backhome_tasks(user: dict) -> None:
    """按已保存业务事实恢复任务；跨日／周仅重置对应任务的领奖状态。"""
    from gameplay_data import client_catalog
    if 'backhome' not in user:
        return
    state = backhome_state(user)
    day = business_day(int(time.time()))
    periods = {901: day, 902: (day + 3) // 7}
    saved = state.setdefault('task_periods', {})
    for cfg in client_catalog()['tasks']:
        kind = cfg['type']
        if kind == 5 and cfg['condition'] == 300 and cfg['additional_parameter'] == [39]:
            stats = state.get('weekly_events', {})
            if stats.get('period') == (day + 3) // 7:
                value = stats.get('counts', {}).get('300', 0)
                entry = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
                if not entry['complete_flag']:
                    entry['progress'] = max(entry['progress'], min(cfg['need'], value))
        if kind not in (901, 902, 903):
            continue
        if kind in periods and saved.get(str(kind), periods[kind]) != periods[kind]:
            user.setdefault('tasks', {}).pop(str(cfg['id']), None)
        value = 0
        if kind in (901, 902):
            stats = state.get('daily_events' if kind == 901 else 'weekly_events', {})
            if stats.get('period') == periods[kind]:
                value = stats.get('counts', {}).get(str(cfg['condition']), 0)
        elif cfg['condition'] == 2012:
            args = cfg['additional_parameter']
            value = int(state['idol']['pve'].get(str(args[0]), {}).get('score', 0) >= args[1])
        entry = user.setdefault('tasks', {}).setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        if not entry['complete_flag']:
            entry['progress'] = min(cfg['need'], value)
    for kind, period in periods.items():
        saved[str(kind)] = period
