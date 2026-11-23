"""大厅任务进度的事实来源：按存档与真实战绩计算，累加类条件使用独立计数器。

新手课程（``NOOB_3_0``，任务类型 603）与入职活动的日常委托（``DAILY_OFFER``，任务类型 10）
原先只依赖事件钩子推进进度，绝大多数条件没有任何钩子，任务面板因此长期停在 0。
本模块按任务配置的 ``condition`` 与 ``additional_parameter`` 逐条换算当前进度：

- 成长类（等级、技能、权钥、刻印、阶位）直接读角色与刻印存档；
- 通关类（主线、物资关、刻印关、历战轮回、梦境再构）读 ``battle_progress`` 与挑战存档；
- 累加类（吨吨值/艾因索菲币消耗、探测次数、刻印突破/赋能、商店购买、每日任务完成数）
  使用 ``task_counters`` 中的独立累计值，由对应操作写入，见 ``bump``。

本服未实现对应玩法的条件（刻印推荐浏览、黑区净化、因果观测、公会）没有可核对的事实，
按私有服口径直接判定达成，集中在 ``GRANTED_CONDITIONS``，便于日后接入原玩法时改为
真实结算。角色语音浏览由 ``32044`` 播放请求计数，属真实结算。
"""

from functools import lru_cache
import time

from .gameplay_protocol import read_client_data

# 需要按事实重算进度的任务类型：日常委托与各版本新手课程。
TRACKED_TYPES = frozenset({10, 603, 604, 605, 606})
NEWBIE_TASK_TYPES = frozenset({601, 602, 603, 604, 605, 606})

# 条件编号见客户端 AssignmentCfg 的 condition 字段。
LEVEL = 2
CURRENCY_EXCHANGE = 3
POSTER_TOUCH = 8
DISPATCH = 9
FATIGUE_TEA = 10
HERO_LEVEL = 102
HERO_BREAK = 103
HERO_SKILL_TOTAL = 106
HERO_TRUST = 109
GIFT = 112
EQUIP_RECOMMEND_VIEW = 114
HERO_VOICE_VIEW = 115
HERO_STORY_VIEW = 116
POSTER_CHANGE = 118
ASTROLABE_ALL = 120
EQUIP_LEVEL = 200
EQUIP_BREAK = 201
EQUIP_ENCHANT = 204
WEAPON_LEVEL = 206
WEAPON_BREAK = 207
SERVANT_STAGE = 209
HERO_SKILL_LEVEL = 217
TASK_REWARD_POINT = 300
FIVE_STAR_EQUIP = 301
SPEND = 350
STAGE_CLEAR = 401
STAGE_GROUP_CLEAR = 405
HERO_TEACH = 406
TOWER_CLEAR = 440
TOWER_LAYER = 442
PASSPORT_CHALLENGE = 453
DARK_ZONE = 454
CAUSAL_EXPLORE = 463
DREAM_BOSS = 472
DREAM_STAR_REWARD = 473
FIVE_STAR_DROP = 481
DAILY_TASK_DONE = 500
SHOP_BUY = 600
DRAW = 700
CLUB = 903

# 本服没有对应玩法的条件：直接判定达成，接入原玩法后请改为真实结算。
# 语音浏览（115）已由 32044 播放语音请求真实计数，不在此列。
GRANTED_CONDITIONS = frozenset({EQUIP_RECOMMEND_VIEW, DARK_ZONE, CAUSAL_EXPLORE, CLUB})


def counter_key(condition: int, parameter: int = 0) -> str:
    """累加计数按条件与参数分桶，避免不同消耗类型互相污染。"""
    return f'{int(condition)}:{int(parameter)}'


def bump(user: dict, condition: int, parameter: int = 0, amount: int = 1) -> None:
    """写入累加条件计数；数量非正时忽略，重复调用按次累加。"""
    if amount <= 0:
        return
    store = user.setdefault('task_counters', {})
    key = counter_key(condition, parameter)
    store[key] = int(store.get(key, 0)) + int(amount)


def counter(user: dict, condition: int, parameter: int = 0) -> int:
    """读取累加条件计数，未记录时为零。"""
    return int((user.get('task_counters') or {}).get(counter_key(condition, parameter), 0))


def counter_total(user: dict, condition: int) -> int:
    """汇总同一条件所有分桶的累计值，供不限定来源的任务使用。"""
    prefix = f'{int(condition)}:'
    return sum(int(value) for key, value in (user.get('task_counters') or {}).items()
               if key.startswith(prefix))


# 由任务计数器承载的累加条件，其余条件由存档事实直接换算。
COUNTER_CONDITIONS = frozenset({CURRENCY_EXCHANGE, POSTER_TOUCH, DISPATCH, FATIGUE_TEA,
                                EQUIP_ENCHANT, TASK_REWARD_POINT, SPEND, PASSPORT_CHALLENGE,
                                POSTER_CHANGE, FIVE_STAR_DROP, DAILY_TASK_DONE, SHOP_BUY,
                                HERO_VOICE_VIEW})


def counter_parameter(cfg: dict) -> int:
    """累加条件使用的分桶参数；空串、列表等非整数参数一律按 0 处理。"""
    params = cfg.get('additional_parameter') or []
    value = params[0] if params else 0
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, int(value))


def _owned_heroes(user: dict) -> list[dict]:
    """只统计真正拥有的角色，皮肤占位角色不参与条件判断。"""
    return [h for h in user.get('heroes', []) if h.get('unlock', 1)]


@lru_cache(maxsize=1)
def fact_catalog() -> dict:
    """一次读取条件换算需要的角色技能、神格节点归属与章节关卡归属。"""
    return read_client_data('''(function()
        local r={heroes={},astrolabe_total={},chapters={}}
        local h=require('HeroCfg')
        for _,id in ipairs(h.all) do
            local v=h[id]
            r.heroes[tostring(id)]={skills=v.skills,astrolabe=v.astrolabe}
        end
        local a=require('HeroAstrolabeCfg')
        for _,id in ipairs(a.all) do
            local v=a[id]
            local suit=v.hero_astrolabe_suit_id
            r.astrolabe_total[tostring(suit)]=(r.astrolabe_total[tostring(suit)] or 0)+1
        end
        -- 任务里的关卡组参数（2011、202、402 等）是章节 ID，需要展开成实际关卡。
        local c=require('ChapterCfg')
        for _,id in ipairs(c.all) do
            local v=c[id]
            if v.section_id_list and #v.section_id_list > 0 then
                r.chapters[tostring(id)]=v.section_id_list
            end
        end
        return r
    end)()''')


def _skill_total(hero: dict, cache: dict) -> int:
    """角色技能等级之和，越过跃迁附加等级，与星级超越加级一致。"""
    from .hero_training_data import total_skill_level
    skills = fact_catalog()['heroes'].get(str(hero['id']), {}).get('skills') or []
    return sum(1 if skill % 1000 == 305 else total_skill_level(hero, skill) for skill in skills)


def _skill_max(hero: dict) -> int:
    """角色单技能最高等级，用于“任意技能等级达到”类条件。"""
    from .hero_training_data import skill_level
    skills = fact_catalog()['heroes'].get(str(hero['id']), {}).get('skills') or []
    return max((skill_level(hero, skill) for skill in skills), default=0)


def _weapon_level(hero: dict) -> int:
    """按权钥累计经验和当前突破上限换算强化等级。"""
    from .hero_training_data import exp_level, training_catalog
    limits = training_catalog()['settings']['weapon_exp_limit']
    index = min(int(hero.get('weapon_break', 0)), len(limits) - 1)
    return exp_level(int(hero.get('weapon_exp', 0)), int(limits[index]), True)


def _equip_level(equip: dict) -> int:
    """读取刻印强化等级，配置缺失的实例按零处理。"""
    from .equip_cultivation import equip_level
    from .battle_rewards import reward_item
    if not reward_item(equip['prefab_id'])['equip']:
        return 0
    return equip_level(equip)


def _equip_star(equip: dict) -> int:
    """读取刻印星级，非刻印实例为零。"""
    from .battle_rewards import reward_item
    return int((reward_item(equip['prefab_id'])['equip'] or {}).get('starlevel', 0))


def _stage_clear_times(user: dict, stage: int) -> int:
    """汇总指定关卡的真实通关次数；参数是章节 ID 时展开为其全部关卡。

    任务配置里 2011、202、402 这类参数是 ``ChapterCfg`` 的章节 ID（物资关、刻印关、
    角色研习等），真正的通关记录按章节内的具体关卡保存，必须先展开再统计。
    """
    stages = fact_catalog()['chapters'].get(str(stage))
    keys = {str(section) for section in stages} if stages else {str(stage)}
    return sum(int(row.get('clear_times', 0)) for name, row in user.get('battle_progress', {}).items()
               if name.split(':')[-1] in keys)


def _battle_rows(user: dict, kinds: tuple, cache: dict) -> list[dict]:
    """按玩法类型取真实通关记录，供多次查询复用。"""
    key = ('battle', kinds)
    if key not in cache:
        cache[key] = [dict(row, kind=int(name.split(':')[0]), stage=int(name.split(':')[-1]))
                      for name, row in user.get('battle_progress', {}).items()
                      if int(name.split(':')[0]) in kinds]
    return cache[key]


def _boss_groups(cache: dict) -> list[int]:
    """梦境再构的首领关卡组，用于识别残响难度通关。"""
    if 'boss_groups' not in cache:
        from .challenge_data import challenge_catalog
        groups = challenge_catalog()['boss_groups']
        cache['boss_groups'] = {int(stage) for rows in groups.values() for stage in rows}
    return cache['boss_groups']


def facts_value(user: dict, cfg: dict, cache: dict | None = None) -> int | None:
    """按任务条件换算当前进度；返回 ``None`` 表示本模块不接管该条件。"""
    cache = {} if cache is None else cache
    kind = int(cfg['condition'])
    params = cfg.get('additional_parameter') or []
    heroes = _owned_heroes(user)
    if kind in GRANTED_CONDITIONS:
        return int(cfg['need'])
    if kind == LEVEL:
        return int(user.get('level', 1))
    if kind == SHOP_BUY:
        # 购买事件按商店号分桶，配置参数为 0 的“任意商店”任务需要汇总全部商店。
        parameter = counter_parameter(cfg)
        return counter_total(user, kind) if not parameter else counter(user, kind, parameter)
    if kind in (CURRENCY_EXCHANGE, POSTER_TOUCH, DISPATCH, FATIGUE_TEA, TASK_REWARD_POINT,
                PASSPORT_CHALLENGE, EQUIP_ENCHANT, POSTER_CHANGE, FIVE_STAR_DROP,
                HERO_VOICE_VIEW):
        return counter(user, kind, counter_parameter(cfg))
    if kind == HERO_LEVEL:
        need = int(params[1]) if len(params) > 1 else 0
        return sum(1 for h in heroes if int(h.get('level', 0)) >= need)
    if kind == HERO_BREAK:
        return sum(max(0, int(h.get('break_level', 0))) for h in heroes)
    if kind == HERO_SKILL_TOTAL:
        if 'skill_total' not in cache:
            cache['skill_total'] = max((_skill_total(h, cache) for h in heroes), default=0)
        return cache['skill_total']
    if kind == HERO_TRUST:
        need = int(params[1]) if len(params) > 1 else 0
        return int(any(int(h.get('trust_level', 0)) >= need for h in heroes))
    if kind == GIFT:
        return int(any(archive.get('gift_list') for archive in (user.get('archives') or {}).values()
                       if isinstance(archive, dict)))
    if kind == HERO_STORY_VIEW:
        return min(1, len(user.get('read_stories') or []))
    if kind == ASTROLABE_ALL:
        totals = fact_catalog()['astrolabe_total']
        for hero in heroes:
            suits = fact_catalog()['heroes'].get(str(hero['id']), {}).get('astrolabe') or []
            total = sum(int(totals.get(str(suit), 0)) for suit in suits)
            if total and len(set(hero.get('astrolabes') or [])) >= total:
                return 1
        return 0
    if kind == EQUIP_LEVEL:
        need = int(params[0]) if params else 0
        if 'equip_levels' not in cache:
            cache['equip_levels'] = [_equip_level(e) for e in user.get('equipment', [])]
        return sum(1 for level in cache['equip_levels'] if level >= need)
    if kind == EQUIP_BREAK:
        return sum(max(0, int(e.get('now_break_level', 0))) for e in user.get('equipment', []))
    if kind == WEAPON_LEVEL:
        need = int(params[0]) if params else 0
        if 'weapon_levels' not in cache:
            cache['weapon_levels'] = [_weapon_level(h) for h in heroes]
        return sum(1 for level in cache['weapon_levels'] if level >= need)
    if kind == WEAPON_BREAK:
        return sum(max(0, int(h.get('weapon_break', 0))) for h in heroes)
    if kind == SERVANT_STAGE:
        return sum(max(0, int(s.get('stage', 1)) - 1) for s in user.get('servants', []))
    if kind == HERO_SKILL_LEVEL:
        need = int(params[1]) if len(params) > 1 else 0
        return int(any(_skill_max(h) >= need for h in heroes))
    if kind == FIVE_STAR_EQUIP:
        if 'equip_stars' not in cache:
            cache['equip_stars'] = [_equip_star(e) for e in user.get('equipment', [])]
        return sum(1 for star in cache['equip_stars'] if star >= 5)
    if kind == SPEND:
        return counter(user, kind, counter_parameter(cfg))
    if kind == STAGE_CLEAR:
        return int(bool(params) and _stage_clear_times(user, params[0]) > 0)
    if kind == STAGE_GROUP_CLEAR:
        return sum(_stage_clear_times(user, stage) for stage in params)
    if kind == HERO_TEACH:
        return _hero_teach_count(user, cache)
    if kind in (TOWER_CLEAR, TOWER_LAYER):
        rows = _battle_rows(user, (7,), cache)
        if kind == TOWER_CLEAR:
            return len({row['stage'] for row in rows if row.get('clear_times')})
        return int(any(row['stage'] == int(params[-1]) and row.get('clear_times') for row in rows)
                   if params else False)
    if kind == DREAM_BOSS:
        groups = _boss_groups(cache)
        return sum(1 for row in _battle_rows(user, (10,), cache)
                   if row.get('clear_times') and row['stage'] in groups)
    if kind == DREAM_STAR_REWARD:
        star = int(params[1]) if len(params) > 1 else 0
        return sum(1 for claimed in (user.get('boss_star_rewards') or []) if int(claimed) >= star)
    if kind == DAILY_TASK_DONE:
        return counter(user, kind, counter_parameter(cfg))
    if kind == DRAW:
        return sum(int(pool.get('total', 0)) for pool in (user.get('draw') or {}).get('pools', {}).values())
    return None


def _hero_teach_count(user: dict, cache: dict) -> int:
    """统计已完成角色教学的个数，研习关与试用关共用同一角色归属。"""
    if 'teach_heroes' not in cache:
        from .battle_progress import progress_catalog
        catalog = progress_catalog()
        covered = set()
        for cfg in catalog['hero_teach']:
            if user.get('battle_progress', {}).get(f"13:{cfg['id']}", {}).get('clear_times'):
                covered.add(cfg['hero_list'][0][0])
        for key, row in user.get('battle_progress', {}).items():
            kind, stage_id = map(int, key.split(':'))
            if kind in (20, 76) and row.get('clear_times'):
                hero = catalog.get('trial_heroes', {}).get(str(stage_id))
                if hero is not None:
                    covered.add(hero)
        cache['teach_heroes'] = covered
    return len(cache['teach_heroes'])


NEWBIE_TASK_TYPE = 603


def newbie_max_phase(user: dict, now: int | None = None) -> int:
    """新手课程已开放到第几天：领完当天全部任务即开放次日，且不早于已过的自然日。

    客户端 ``ActivityNoobData:GetUnlockPhase`` 以 ``max_phase`` 作为阶段上限，
    服务端原先固定下发 1，第 2 天以后始终锁着。这里按“连续完成的天数”推进，
    同时按自然日兜底，避免必须跨天才能继续。
    """
    from .gameplay_data import client_catalog
    from .server_clock import business_day
    catalog = client_catalog()['tasks']
    saved = user.get('tasks', {})
    phases = sorted({cfg['phase'] for cfg in catalog if cfg['type'] == NEWBIE_TASK_TYPE})
    if not phases:
        return 1
    opened = phases[0]
    for phase in phases[:-1]:
        rows = [cfg for cfg in catalog if cfg['type'] == NEWBIE_TASK_TYPE and cfg['phase'] == phase]
        if not rows or not all(saved.get(str(cfg['id']), {}).get('complete_flag') for cfg in rows):
            break
        opened = phase + 1
    trigger = int((user.get('newbie') or {}).get('trigger_time') or user.get('created_at', 0))
    days = business_day(int(time.time()) if now is None else now) - business_day(trigger) + 1 if trigger else 1
    elapsed = min(phases[-1], max(phases[0], days))
    return int(min(phases[-1], max(opened, elapsed, phases[0])))


def reconcile_tasks(user: dict) -> None:
    """把可核对的任务进度补到当前事实，已领取的任务保持原样。"""
    from .gameplay_data import client_catalog
    saved = user.setdefault('tasks', {})
    cache = {}
    for cfg in client_catalog()['tasks']:
        if cfg['type'] not in TRACKED_TYPES:
            continue
        entry = saved.setdefault(str(cfg['id']), {'progress': 0, 'complete_flag': 0})
        if entry.get('complete_flag'):
            continue
        if int(cfg['condition']) in COUNTER_CONDITIONS:
            # 旧存档只有任务内的进度，没有独立计数：先把已有进度补进计数器，
            # 让后续新增的同类累计任务继承此前真实累计的量。
            parameter = counter_parameter(cfg)
            stored = int(entry.get('progress', 0))
            current = counter(user, int(cfg['condition']), parameter)
            if stored > current:
                bump(user, int(cfg['condition']), parameter, stored - current)
        value = facts_value(user, cfg, cache)
        if value is None:
            continue
        entry['progress'] = min(int(cfg['need']), max(int(entry.get('progress', 0)), int(value)))
