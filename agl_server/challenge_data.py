"""挑战玩法的本地排期与初始化；关卡取自安装包配置。"""

from functools import lru_cache
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


def core_reward_catalog() -> dict:
    """读取客户端迭代校验奖励表，任务 ID 与当前安装包版本保持一致。"""
    rows = read_client_data('''(function()
        local cfg=require('CoreVerificationRewardCfg')
        local result={}
        for _,id in ipairs(cfg.all) do
            local row=cfg[id]
            local rewards={}
            for _,pair in ipairs(row.reward or {}) do
                rewards[#rewards+1]={id=pair[1],num=pair[2]}
            end
            result[#result+1]={id=row.id,cycle=row.cycle,reward_type=row.reward_type,
                               condition=row.condition,rewards=rewards}
        end
        return result
    end)()''')
    return {int(row['id']): {'id': int(row['id']), 'cycle': int(row['cycle'] or 0),
                             'reward_type': int(row['reward_type'] or 0),
                             'condition': row.get('condition'),
                             'rewards': [{'id': int(x['id']), 'num': int(x['num'])}
                                         for x in row.get('rewards') or []]}
            for row in rows}


def core_cycle_info() -> tuple[int, int, int]:
    """按当前活动周期计算迭代校验的当期、下期与刷新时间。"""
    rows = challenge_catalog()['core']
    cycles = list(dict.fromkeys(row['cycle'] for row in rows))
    settings = period_settings()
    start, end = period_window('core', settings)
    cfg = settings['core']
    index = ((start - cfg['anchor']) // (cfg['hours'] * 3600)) % len(cycles)
    return cycles[index], cycles[(index + 1) % len(cycles)], end


def core_claimed_task_ids(user: dict, cycle: int) -> list[int]:
    """返回永久首通与当期已领取的任务 ID，供 sc_75009 标记已领。"""
    state = user.get('core_verification_claimed') or {}
    if not isinstance(state, dict):
        return []
    result = []
    for key in ('0', str(int(cycle))):
        for task_id in state.get(key) or []:
            try:
                result.append(int(task_id))
            except (TypeError, ValueError):
                continue
    return sorted(set(result))


def core_stage_records(user: dict, cycle: int) -> list[dict]:
    """返回当期已通关的迭代校验关卡，包含 Boss 与难度。"""
    return [row for row in challenge_catalog()['core']
            if int(row['cycle']) == int(cycle)
            and progress(user, 67, int(row['id'])).get('clear_times')]


def claim_core_rewards(user: dict, reward_ids) -> list[dict]:
    """按真实通关进度领取 75006 任务，返回实际发放的道具列表。"""
    now_cycle, _, _ = core_cycle_info()
    catalog = core_reward_catalog()
    records = core_stage_records(user, now_cycle)
    max_diff1 = max([int(row['difficult']) for row in records if int(row['boss_type']) == 1] or [0])
    max_diff2 = max([int(row['difficult']) for row in records if int(row['boss_type']) == 2] or [0])
    min_diff = min(max_diff1, max_diff2)
    state = user.get('core_verification_claimed')
    if not isinstance(state, dict):
        state = {}
        user['core_verification_claimed'] = state

    granted = []
    for raw_id in reward_ids or []:
        try:
            task_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        task = catalog.get(task_id)
        if not task:
            continue
        reward_type = int(task['reward_type'])
        task_cycle = int(task['cycle'])
        if reward_type in (1, 2) and task_cycle != now_cycle:
            continue
        if reward_type not in (1, 2, 3, 4):
            continue

        claim_cycle = 0 if reward_type == 4 else now_cycle
        claimed = state.setdefault(str(claim_cycle), [])
        if task_id in claimed:
            continue

        can_claim = False
        if reward_type == 1:
            required = task_id % 10
            can_claim = required > 0 and max_diff1 >= required
        elif reward_type == 2:
            required = task_id % 10
            can_claim = required > 0 and max_diff2 >= required
        elif reward_type == 3:
            required = task_id - 1000
            can_claim = required > 0 and min_diff >= required
        elif reward_type == 4:
            if 2001 <= task_id <= 2008:
                can_claim = min_diff >= task_id - 2000
            elif 2011 <= task_id <= 2018:
                can_claim = max_diff1 >= task_id - 2010
            elif 2021 <= task_id <= 2028:
                can_claim = max_diff2 >= task_id - 2020

        if not can_claim:
            continue
        grant_rewards(user, task['rewards'])
        claimed.append(task_id)
        granted.extend(task['rewards'])
    return granted


def core_data(user: dict) -> dict:
    """按客户端配置顺序轮换迭代校验，预告下期并只恢复当期进度。"""
    rows = challenge_catalog()['core']
    cycle, next_cycle, end = core_cycle_info()
    return {'now_cycle': cycle, 'next_cycle': next_cycle,
            'refresh_timestamp': end, 'max_score_info': {},
            'reward_list': core_claimed_task_ids(user, cycle),
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
