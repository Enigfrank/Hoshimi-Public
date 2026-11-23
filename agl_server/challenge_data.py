"""挑战玩法的本地排期与初始化；关卡取自安装包配置。"""

from functools import lru_cache
import datetime
import random
import time

from .gameplay_protocol import read_client_data
from .polyhedron_game import polyhedron_data
from .equip_exploration import exploration_pushes
from .battle_progress import progress, tower_progress
from .battle_rewards import spend_items, grant_rewards
from .server_clock import business_day
from .activity_periods import period_settings, period_window


@lru_cache(maxsize=1)
def challenge_catalog() -> dict:
    """提取挑战所需的配置，避免下发无效关卡或测试 ID。"""
    return read_client_data('''(function()
        local r={}
        local function rows(name)
            local c=require(name); local t={}
            for _,id in ipairs(c.all) do t[#t+1]=c[id] end
            return t
        end
        r.mythic=rows('MythicNormalCfg')
        r.mythic_stages=require('BattleMythicStageCfg').all
        r.mythic_affixes={}
        for _,id in ipairs({101,102,103}) do
            local affix=require('AffixTypeCfg')[id]
            assert(affix and affix.max_level>=1)
            r.mythic_affixes[#r.mythic_affixes+1]={id=id,level=1,type=0}
        end
        r.final=rows('MythicFinalCfg')
        r.boss=rows('BossChallengeCfg')
        r.boss_groups={}
        for _,area in ipairs(r.boss) do for _,id in ipairs(area.boss_list) do
            r.boss_groups[tostring(id)]=require('StageGroupCfg')[id].stage_list
        end end
        r.advance=rows('BossChallengeAdvanceCfg')
        r.advance_pool=rows('BossChallengeAdvancePoolCfg')
        r.seizure=require('BattleEquipSeizureStageCfg').all
        r.enchant_groups={}
        local groups=require('StageGroupCfg')
        for _,id in ipairs(groups.all) do
            if groups[id].type==8 then
                r.enchant_groups[#r.enchant_groups+1]={id=id,level=groups[id].level,stage_list=groups[id].stage_list}
            end
        end
        local g=require('GameSetting')
        r.enchant_free=g.fm_get_refresh_count.value[1]
        r.enchant_buy_limit=g.fm_item_refresh_limit_count.value[1]
        r.enchant_cost=g.fm_item_refresh_cost.value
        r.abyss=rows('AbyssCfg')
        r.core=rows('CoreVerificationInfoCfg')
        r.polyhedron=rows('PolyhedronHeroShelvesCfg')
        r.chess=rows('WarchessLevelCfg')
        r.rogue=rows('RogueTeamCfg')
        return r
    end)()''')


def _enchant_groups() -> list:
    """返回全部联合特勤关卡组及其品级和关卡列表。"""
    return challenge_catalog()['enchant_groups']


def _enchant_levels() -> dict:
    """关卡组 ID 到 B/A/S 品级的映射。"""
    return {group['id']: group['level'] for group in _enchant_groups()}


def _roll_enchant() -> list:
    """随机抽取三个不重复的关卡组，并按品级排序后下发。"""
    ids = [group['id'] for group in _enchant_groups()]
    levels = _enchant_levels()
    return sorted(random.sample(ids, 3), key=lambda group_id: levels[group_id])


def _roll_enchant_slot(current: list, index: int) -> None:
    """原地重掷该栏，保留槽位位置和其余两项。"""
    candidates = [group['id'] for group in _enchant_groups() if group['id'] not in current]
    if candidates:
        current[index] = random.choice(candidates)


def seizure_affix_ids(stage_id: int) -> list[int]:
    """按当日关卡的属性顺序取官方介质攫取三件套增益。

    9201~9208 是八种属性偏移场，9215~9222 是对应的屏蔽无效化，
    9210 是死徒；客户端只认 ActivityAffixPoolCfg 里的这三个 ID。
    """
    stages = challenge_catalog()['seizure']
    element = stages.index(stage_id) % 8 if stage_id in stages else 0
    return [9201 + element, 9215 + element, 9210]


def next_seizure_affix_refresh() -> int:
    """下一个周一/周四/周六 05:00，和官服增益轮换一致。"""
    now = datetime.datetime.now()
    for offset in range(8):
        candidate = (now + datetime.timedelta(days=offset)).replace(
            hour=5, minute=0, second=0, microsecond=0)
        if candidate > now and candidate.weekday() in (0, 3, 5):
            return int(candidate.timestamp())
    return int(now.timestamp()) + 86400


def _seizure_effective_dt(now: int | None = None) -> datetime.datetime:
    """05:00 前仍算前一天，和每日/每期刷新口径一致。"""
    now = int(time.time()) if now is None else int(now)
    value = datetime.datetime.fromtimestamp(now)
    if value.hour < 5:
        value -= datetime.timedelta(days=1)
    return value


def seizure_week_key(now: int | None = None) -> int:
    """返回本期开始时间：最近一个周四 05:00（官服周常周四换期）。"""
    value = _seizure_effective_dt(now)
    days_since_thursday = (value.weekday() - 3) % 7
    thursday = (value - datetime.timedelta(days=days_since_thursday)).replace(
        hour=5, minute=0, second=0, microsecond=0)
    return int(thursday.timestamp())


def next_seizure_week_refresh(now: int | None = None) -> int:
    """下一个周四 05:00；官服介质攫取页面底部按这个周期刷新。"""
    return seizure_week_key(now) + 7 * 86400


def seizure_challenge_rate(now: int | None = None) -> float:
    """周六/周日 05:00 后 1.2 倍，其余时间 1.0 倍。"""
    return 1.2 if _seizure_effective_dt(now).weekday() in (5, 6) else 1.0


def seizure_rewards() -> list:
    """读取客户端介质攫取积分奖励档位，避免硬编码分数和道具。"""
    return read_client_data('''(function()
        local c=require('EquipSeizurePointRewardCfg')
        local t={}
        for _,id in ipairs(c.all) do t[#t+1]=c[id] end
        return t
    end)()''')


def seizure_state(user: dict, now: int | None = None) -> dict:
    """惰性重置今日最高分和本期累计分，并返回介质攫取存档。"""
    now = int(time.time()) if now is None else int(now)
    state = user.setdefault('equip_seizure', {})
    state.setdefault('today_max_score', 0)
    state.setdefault('sum_score', 0)
    state.setdefault('got_reward_id_list', [])
    state.setdefault('last_battle_id', 0)
    state.setdefault('stage_id', 0)
    day = business_day(now)
    week = seizure_week_key(now)
    if state.get('day') != day:
        state['day'] = day
        state['today_max_score'] = 0
    if state.get('week_key') != week:
        state['week_key'] = week
        state['sum_score'] = 0
        state['got_reward_id_list'] = []
    return state


def calc_equip_seizure_score(wave_count: int = 0, enemy_dead: int = 0,
                             battle_time_ms: int = 0, explicit_score: int = 0,
                             is_win: bool = False) -> tuple[int, int]:
    """按官方波次分计算介质攫取基础分。

    官服 42 波未击杀 Boss 的结算为 19,080（周日 1.2 倍），即基础 15,900：
    每波 300 分，20/30/40 波各追加 1,000/1,100/1,200 分。
    11 波 3,300、31 波 11,400、39 波 16,560 也都与这个口径吻合。
    """
    if explicit_score > 0:
        return int(explicit_score), 0
    if wave_count > 0:
        waves = int(wave_count)
    elif enemy_dead > 0:
        if enemy_dead <= 91:
            waves = max(1, int(round(enemy_dead / 4.8)))
        else:
            waves = max(1, int(round(19 + ((enemy_dead - 91) / 20.2) * 5)))
    elif battle_time_ms > 0:
        seconds = battle_time_ms / 1000.0 if battle_time_ms > 1000 else float(battle_time_ms)
        waves = max(1, int(seconds / 15.0))
    elif is_win:
        waves = 40
    else:
        waves = 1
    score = waves * 300
    if waves >= 20:
        score += 1000
    if waves >= 30:
        score += 1100
    if waves >= 40:
        score += 1200
    return min(24000, max(200, score)), waves


def seizure_wave_count(info: dict) -> int:
    """从原生战斗报告读取到达波次。

    14 号记录是关卡信息，第 2 项 42 是本关总波次，不是玩家到达波次；
    15 号记录的第 2 项才是玩家实际到达波次（例如 39）。
    若 15 号缺失，才退回用 14 号，并用总波次做上限。
    """
    if not isinstance(info, dict):
        return 0

    def _int_at(key):
        values = info.get(key)
        if isinstance(values, (list, tuple)) and len(values) >= 2:
            try:
                return int(values[1])
            except (TypeError, ValueError):
                return 0
        return 0

    total = _int_at('14')
    reached = _int_at('15')
    if reached > 0:
        # 通关瞬间客户端可能上报“总波次+1”，用关卡总波次封顶。
        if total > 0 and reached > total:
            return total
        if 1 <= reached <= 100:
            return reached
    if 0 < total <= 100:
        return total
    return 0


def settle_equip_seizure(user: dict, battle: dict, result: int | None = None,
                         info: dict | None = None, now: int | None = None) -> dict:
    """写入介质攫取分数；今日只累计最高分，本期累计按刷新后的增量增加。"""
    if not battle or battle.get('common', {}).get('type') != 53:
        return {}
    cached = battle.get('seizure_settlement')
    if cached and cached.get('battle_id') == battle.get('id'):
        return cached
    reported = battle.get('reported') or {}
    result = int(reported.get('result', 0) if result is None else result)
    if info is None:
        info = reported.get('info') or {}
    now = int(time.time()) if now is None else int(now)
    state = seizure_state(user, now)
    score = 0
    waves = 0
    if result == 1:
        raw_score, waves = calc_equip_seizure_score(
            wave_count=seizure_wave_count(info),
            enemy_dead=int(info.get('11') or 0),
            battle_time_ms=int(info.get('2') or 0),
            explicit_score=int(info.get('score') or info.get('point') or 0),
            is_win=True)
        score = int(round(raw_score * seizure_challenge_rate(now)))
        old_today = int(state.get('today_max_score') or 0)
        state['today_max_score'] = max(old_today, score)
        state['sum_score'] = int(state.get('sum_score') or 0) + max(0, score - old_today)
    state['last_battle_id'] = int(battle.get('id') or 0)
    state['last_score'] = score
    state['last_battle_time'] = now
    battle['seizure_settlement'] = {
        'battle_id': int(battle.get('id') or 0), 'score': score,
        'raw_score': int(score / seizure_challenge_rate(now)) if score else 0,
        'est_waves': waves, 'is_valid': result == 1,
        'today_max_score': int(state.get('today_max_score') or 0),
        'sum_score': int(state.get('sum_score') or 0),
        'challenge_rate': seizure_challenge_rate(now),
    }
    return battle['seizure_settlement']


def restore_equip_seizure_battle(user: dict, now: int | None = None) -> None:
    """把修复前已打完但未计分的最近一局补写进存档，且同一局只补一次。"""
    session = user.get('battle_session') or {}
    if session.get('common', {}).get('type') != 53:
        return
    reported = session.get('reported') or {}
    if reported.get('result') != 1:
        return
    state = user.get('equip_seizure') or {}
    if int(state.get('last_battle_id') or 0) == int(session.get('id') or 0):
        return
    finished = (session.get('settlement') or {}).get('battle_result', {}).get('timestamp')
    finished = int(finished or session.get('started') or now or time.time())
    if business_day(finished) != business_day(int(time.time()) if now is None else int(now)):
        return
    settle_equip_seizure(user, session, now=finished)


def seizure_data(user: dict, now: int | None = None) -> dict:
    """构造 sc_35011，分数和领奖状态全部来自玩家存档而不是常量。"""
    now = int(time.time()) if now is None else int(now)
    restore_equip_seizure_battle(user, now)
    state = seizure_state(user, now)
    stages = challenge_catalog()['seizure']
    stage_id = int(state.get('stage_id') or stages[0])
    state['stage_id'] = stage_id
    return {
        'stage_id': stage_id,
        'challenge_rate': seizure_challenge_rate(now),
        'affix_info': {'affix_id_list': seizure_affix_ids(stage_id),
                       'refresh_timestamp': next_seizure_affix_refresh()},
        'today_max_score': int(state.get('today_max_score') or 0),
        'sum_score': int(state.get('sum_score') or 0),
        'got_reward_id_list': [int(value) for value in state.get('got_reward_id_list') or []],
        'refresh_timestamp': next_seizure_week_refresh(now),
    }


def claim_equip_seizure_rewards(user: dict, id_list: list, now: int | None = None) -> dict:
    """按当期累计分校验档位并发放奖励，单领和一键领取共用这套逻辑。"""
    state = seizure_state(user, now)
    reward_cfg = {int(row['id']): row for row in seizure_rewards()}
    requested = [int(value) for value in (id_list or []) if value]
    claimed = {int(value) for value in state.get('got_reward_id_list') or []}
    totals = {}
    for reward_id in requested:
        row = reward_cfg.get(reward_id)
        if not row or reward_id in claimed:
            continue
        if int(state.get('sum_score') or 0) < int(row.get('need') or 0):
            continue
        for item, amount in row.get('reward_item_list') or []:
            item, amount = int(item), int(amount)
            if item and amount:
                totals[item] = totals.get(item, 0) + amount
        claimed.add(reward_id)
    rewards = [{'id': item, 'num': amount} for item, amount in totals.items()]
    if rewards:
        grant_rewards(user, rewards)
    state['got_reward_id_list'] = sorted(claimed)
    return {'result': 0, 'reward_list': rewards}


def _enchant_state(user: dict, now: int | None = None) -> dict:
    """跨业务日时重掷关卡组并恢复免费刷新次数。"""
    now = int(time.time()) if now is None else now
    state = user.setdefault('enchant', {})
    day = business_day(now)
    if state.get('day') != day or len(state.get('list', [])) != 3:
        state.update({'day': day, 'free_used': 0, 'buy_used': 0, 'list': _roll_enchant()})
    return state


def validate_enchant_stage(user: dict, stage_id: int) -> None:
    """拒绝不属于当日三个任务的联合特勤关卡。"""
    selected = _enchant_state(user)['list']
    if not any(group['id'] in selected and stage_id in group['stage_list']
               for group in _enchant_groups()):
        raise ValueError('联合特勤关卡不属于当前任务')


def enchantment_data(user: dict) -> dict:
    """下发当日抽到的三个关卡组与剩余免费刷新次数。"""
    state = _enchant_state(user)
    return {'free_refreshed_times': max(0, challenge_catalog()['enchant_free'] - state['free_used']),
            'all_buy_refreshed_times': state['buy_used'],
            'enchantment_battle_list': state['list']}


def refresh_enchantment(user: dict, refresh_type: int) -> list:
    """校验免费或购买次数并扣移转之辉，重掷全部三个关卡组。"""
    if refresh_type not in (1, 2):
        raise ValueError('刷新类型无效')
    state = _enchant_state(user)
    catalog = challenge_catalog()
    if refresh_type == 1:
        if state['free_used'] >= catalog['enchant_free']:
            raise ValueError('免费刷新次数已用完')
        state['free_used'] += 1
    else:
        if state['buy_used'] >= catalog['enchant_buy_limit']:
            raise ValueError('购买刷新次数已用完')
        item_id, cost = catalog['enchant_cost'][state['buy_used']]
        spend_items(user, [{'id': int(item_id), 'num': int(cost)}])
        state['buy_used'] += 1
    state['list'] = _roll_enchant()
    return state['list']


def refresh_enchant_slot(user: dict, stage_id: int) -> list | None:
    """完成并领取特勤任务后原地重掷该栏，返回新列表或 None。"""
    state = _enchant_state(user)
    groups = {group['id']: group for group in _enchant_groups()}
    for index, group_id in enumerate(state['list']):
        if stage_id in groups[group_id]['stage_list']:
            _roll_enchant_slot(state['list'], index)
            return state['list']
    return None


def boss_area(user: dict, area_id=None) -> dict:
    """按显式选择、存档周期、账号等级依次返回梦境周期。"""
    catalog = challenge_catalog()['boss']
    selected = area_id if area_id is not None else user.get('boss_area_id')
    if selected is not None:
        try:
            selected = int(selected)
        except (TypeError, ValueError):
            if area_id is not None:
                raise ValueError('梦境周期无效')
        else:
            area = next((row for row in catalog if row['range_id'] == selected), None)
            if area:
                return area
            if area_id is not None:
                raise ValueError('梦境周期无效')
    if user.get('account_template') == 'normal':
        level = user.get('level', 1)
        return next((row for row in catalog if row['level'][0] <= level <= row['level'][1]), catalog[0])
    return next(row for row in catalog if row['range_id'] == 4)


def boss_group_ids(user: dict, area: dict) -> list[int]:
    """返回周期内 BOSS，并将当前选择旋转到启用列表首位。"""
    groups = [int(group_id) for group_id in area['boss_list']]
    try:
        selected = int(user.get('boss_group_id'))
    except (TypeError, ValueError):
        selected = None
    if selected in groups:
        groups.remove(selected)
        groups.insert(0, selected)
    return groups


def boss_activity_options(user: dict) -> dict:
    """返回梦境周期和 BOSS 选项，供 GM 精确切换当前挑战目标。"""
    current = boss_area(user)
    selected = boss_group_ids(user, current)[0]
    catalog = challenge_catalog()
    areas = []
    for area in catalog['boss']:
        bosses = []
        for group_id in area['boss_list']:
            stages = catalog['boss_groups'][str(group_id)]
            bosses.append({
                'id': group_id,
                'current': group_id == selected and area['range_id'] == current['range_id'],
                'stage_ids': stages,
                'cleared_stages': [stage for stage in stages
                                   if progress(user, 10, stage).get('clear_times')],
            })
        areas.append({
            'id': area['range_id'],
            'level_min': area['level'][0],
            'level_max': area['level'][1],
            'boss_limit': area['boss_nums'],
            'boss_list': bosses,
        })
    return {'area_id': current['range_id'], 'boss_group_id': selected, 'areas': areas}


def boss_data(user: dict) -> list[tuple]:
    """初始化梦境普通与扭曲模式，所选层级保存在账号中。"""
    c = challenge_catalog()
    area = boss_area(user)
    active_groups = boss_group_ids(user, area)[:area['boss_nums']]
    advance = next(x for x in c['advance'] if x['type'] == 2)
    groups = []
    for group_id in active_groups:
        stages = c['boss_groups'][str(group_id)]
        completed = [s for s in stages if progress(user, 10, s).get('clear_times')]
        groups.append({'group_id': group_id, 'finish_stage': completed[-1] if completed else 0,
                       'unlock_timestamp': 0,
                       'used_heroes': sorted({h for s in completed for h in progress(user,10,s).get('last_heroes', [])}),
                       'last_heroes_cfg': user.get('boss_teams', {}).get(str(group_id), []),
                       'star_info': [{'stage_id': s, 'star_list': progress(user,10,s).get('stars', [])}
                                     for s in completed]})
    return [
        (45201, {'mode': user.get('boss_mode', 0), 'next_refresh_time': period_window('dream')[1],
                 'difficulty_list': [x['id'] for x in c['advance']
                                     if user.get('account_template') != 'normal' or x['open_condition'] <= user['level']]}),
        (45001, {'area_id': area['range_id'], 'use_times': 0,
                 'boss_challenge_list': groups, 'receive_star_list': user.get('boss_star_rewards', [])}),
        (45101, {'advance_id': advance['id'], 'boss_list': [
            {'id': x['id'], 'unlock_timestamp': 0, 'max_point': 0, 'diffculty_index': 1,
            **user.get('boss_affixes', {}).get(str(x['id']), {})}
            for x in c['advance_pool'][:advance['boss_nums']]]}),
    ]


def rogue_fetters_data() -> dict:
    """初始化当前肉鸽模板的羁绊记录，避免选人页读取空记录。"""
    activity_id = challenge_catalog()['rogue'][0]['fetters_activity_id']
    fetters = read_client_data(f"(function() local c=require('RogueTeamFettersCfg'); "
        f"for _,id in ipairs(c.all) do if c[id].activity_id=={int(activity_id)} then return c[id] end end end)()")
    return {'activity_id': activity_id, 'fetters_id': fetters['id'], 'next_timestamps': 0}


@lru_cache(maxsize=1)
def rogue_selection_catalog() -> dict:
    """读取迭代校验的难度、角色模板和可选词缀配置。"""
    return read_client_data('''(function()
        local result={difficulties=setmetatable({},{json_array=true}),
            standards=setmetatable({},{json_array=true}),
            affixes=setmetatable({},{json_array=true})}
        local difficulty=require('RogueTeamDifficultyCfg')
        local team=require('RogueTeamCfg')
        local standard=require('HeroStandardSystemCfg')
        local affix=require('ActivityAffixPoolCfg')
        local standards,affixes={},{}
        local function add_standard(id)
            if not standards[id] then
                standards[id]=true
                local row=standard[id]
                result.standards[#result.standards+1]={id=id,hero_id=row.hero_id}
            end
        end
        for _,id in ipairs(difficulty.all) do
            local row=difficulty[id]
            result.difficulties[#result.difficulties+1]=row
            if type(row.affix_pool)=='table' then
                for _,affix_id in ipairs(row.affix_pool) do
                    if not affixes[affix_id] then
                        affixes[affix_id]=true
                        result.affixes[#result.affixes+1]=affix[affix_id]
                    end
                end
            end
            for _,template_id in ipairs(team.all) do
                if team[template_id].difficulty_temp==row.template_id then
                    for _,standard_id in ipairs(team[template_id].hero_list) do add_standard(standard_id) end
                end
            end
        end
        return result
    end)()''')


def rogue_score_activity_id(template_id: int) -> int:
    """按当前迭代模板的子活动查找积分奖励活动。"""
    template = next((row for row in challenge_catalog()['rogue'] if row['id'] == template_id), None)
    if template is None:
        raise ValueError('迭代校验模板不存在')
    return read_client_data(f'''(function()
        local activity=require('ActivityCfg')
        for _,id in ipairs(activity[{template['activity_id']}].sub_activity_list) do
            if activity[id].activity_template==82 then return id end
        end
        return 0
    end)()''')


def rogue_score_reward_ids(template_id: int) -> list[int]:
    """返回当前迭代积分活动允许选择的奖励档位。"""
    activity_id = rogue_score_activity_id(template_id)
    return read_client_data(f"require('ActivityPointRewardCfg').get_id_list_by_activity_id[{activity_id}] or {{}}")


def rogue_team_data(user: dict) -> dict:
    """构造迭代校验外部页面所需的完整持久化数据。"""
    template = challenge_catalog()['rogue'][0]
    state = user.get('rogue_team', {})
    return {'template_id': template['id'],
            'difficult': state.get('difficult', 0),
            'collection_list': state.get('collection_list', []),
            'his_difficult': state.get('his_difficult', []),
            'his_avg': state.get('his_avg', []),
            'unlock_collection': state.get('unlock_collection', []),
            'view_collection': state.get('view_collection', []),
            'tree_list': state.get('tree_list', []),
            'last_id': state.get('last_id', 0)}


def core_data(user: dict) -> dict:
    """按客户端配置顺序轮换迭代校验，预告下期并只恢复当期进度。"""
    rows = challenge_catalog()['core']
    cycles = list(dict.fromkeys(row['cycle'] for row in rows))
    settings = period_settings()
    start, end = period_window('core', settings)
    cfg = settings['core']
    index = ((start - cfg['anchor']) // (cfg['hours'] * 3600)) % len(cycles)
    cycle = cycles[index]
    return {'now_cycle': cycle, 'next_cycle': cycles[(index + 1) % len(cycles)],
            'refresh_timestamp': end, 'max_score_info': {},
            'stage_info': [{'id': row['id'], 'sign': 1,
                            'min_time': progress(user, 67, row['id'])['best_time'] * 1000}
                           for row in rows if row['cycle'] == cycle
                           and progress(user, 67, row['id']).get('clear_times')]}


def challenge_pushes(user: dict) -> list[tuple]:
    """初始化挑战页面；深阱使用独立的层级及首领持久化状态。"""
    from .abyss import abyss_data
    c = challenge_catalog()
    refresh = int(time.time()) + 7*86400
    normal = next((x for x in c['mythic'] if x['id'] == user.get('mythic_difficulty', 1)), c['mythic'][0])
    final_id = user.get('mythic_final_difficulty', 1)
    final = next(x for x in c['final'] if x['id'] == final_id)
    runs = user.get('mythic_final_runs', {}).get(str(final_id), {})
    difficulties = []
    for x in c['mythic']:
        stages = c['mythic_stages']
        difficulties.append({'difficulty': x['id'],
                             'main_partition': {'partition': x['main_partition'], 'stage_id': stages[0]},
                             'sub_partition_list': [{'partition': id, 'stage_id': stages[(i+1) % len(stages)]}
                                                    for i, id in enumerate(x['sub_partition_list'])]})
    initial = user.get('account_template') == 'normal'
    unlocked_mythic = ([c['mythic'][0]['id']] if initial else [x['id'] for x in c['mythic']] + [1001])
    unlocked_final = [c['final'][0]['id']] if initial else [x['id'] for x in c['final']]
    return [
        (42001, enchantment_data(user)),
        *exploration_pushes(user),
        (35011, {'stage_id': c['seizure'][0], 'challenge_rate': 1,
                 'affix_info': {'refresh_timestamp': refresh}, 'refresh_timestamp': refresh}),
        (41001, tower_progress(user)),
        *boss_data(user),
        (44007, {'difficulty_list': difficulties, 'superiority_affix_list': c['mythic_affixes'],
                 'next_refresh_timestamp': refresh}),
        (44009, {'clear_partition_id_list': [int(key.split(':')[1])
                    for key, value in user.get('battle_progress', {}).items()
                    if key.startswith('11:') and value.get('clear_times')],
                 'main_partition_star_list': progress(user, 11, normal['main_partition']).get('stars', []),
                 'star_reward_provide_list': user.get('mythic_star_rewards', {}).get(str(normal['id']), [])}),
        (44019, {'open_difficulty_list': unlocked_mythic,
                 'difficulty': user.get('mythic_difficulty', 1), 'is_new_difficulty': False}),
        (44021, {'stage_list': [{'difficulty_id': x['id'], 'stage_id': [s[0] for s in x['stage_list']]}
                               for x in c['final']]}),
        (44023, {'difficulty_id_can_choose': unlocked_final, 'now_difficulty': final_id,
                 'is_new_difficulty': False, 'receive_reward': user.get('mythic_final_rewards', []),
                 'clear_list': user.get('mythic_final_cleared', []),
                 'challenge_info': [{'team_id': i, 'clear_state': int(all(s in runs.get(str(i), {}).get('stages', []) for s in stages)),
                                     'use_time': runs.get(str(i), {}).get('use_time', 0)}
                                    for i, stages in enumerate(final['stage_list'], 1)]}),
        (55001, abyss_data(user)),
        (75009, core_data(user)),
        (18001, polyhedron_data(user)),
        *([(18005, {'end_info': user['polyhedron_settlement']})]
          if user.get('polyhedron', {}).get('game', {}).get('state') == 3 and user.get('polyhedron_settlement') else []),
        (49001, {'chess_map_list': [{'activity_id': 0, 'chapter': 0,
                                   'chapter_info': [{'chapter_id': x['id_level']} for x in c['chess']
                                                    if x['activity'] == 0]}]}),
        (49023, {'chess_open_info_list': [{'chapter_id': x['id_level'], 'timestamp': 0}
                                         for x in c['chess'] if x['activity'] == 0]}),
        (88305, rogue_team_data(user)),
        (88315, rogue_fetters_data()),
    ]
