"""挑战玩法的本地排期与初始化；关卡取自安装包配置。"""

from functools import lru_cache
import time

from gameplay_protocol import read_client_data
from polyhedron_game import polyhedron_data
from equip_exploration import exploration_pushes
from battle_progress import progress, tower_progress
from activity_periods import period_window


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
        r.enchant=require('StageGroupCfg')[1].stage_list
        r.abyss=rows('AbyssCfg')
        r.core=rows('CoreVerificationInfoCfg')
        r.polyhedron=rows('PolyhedronHeroShelvesCfg')
        r.chess=rows('WarchessLevelCfg')
        r.rogue=rows('RogueTeamCfg')
        return r
    end)()''')


def enchantment_data(user: dict) -> dict:
    """下发联合特勤有效的三档关卡。"""
    return {'free_refreshed_times': 0, 'all_buy_refreshed_times': 0,
            'enchantment_battle_list': challenge_catalog()['enchant']}


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


def challenge_pushes(user: dict) -> list[tuple]:
    """初始化黑区、深阱、梦境、迭代和刻印挑战的页面依赖。"""
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
    abyss = [x for x in c['abyss'] if x['activity_id'] == c['abyss'][0]['activity_id']]
    layers = []
    for x in abyss:
        layers.append({'layer_id': x['level'],
                       'stage_info_list': [{'stage_id': s[1], 'is_completed': bool(progress(user,1003,s[1]).get('clear_times'))}
                                           for s in x['stage_list'] if s[0] != 3],
                       'boss_stage_info_list': [{'stage_id': s[1], 'is_completed': bool(progress(user,1003,s[1]).get('clear_times')),
                                                'boss_hp_rate': 0 if progress(user,1003,s[1]).get('clear_times') else 10000}
                                                for s in x['stage_list'] if s[0] == 3]})
    initial = user.get('account_template') == 'normal'
    unlocked_mythic = ([c['mythic'][0]['id']] if initial else [x['id'] for x in c['mythic']] + [1001])
    unlocked_final = [c['final'][0]['id']] if initial else [x['id'] for x in c['final']]
    completed_layers = [x['level'] for x in abyss
                        if all(progress(user, 1003, s[1]).get('clear_times') for s in x['stage_list'])]
    history_layer = max(completed_layers, default=0) if initial else len(layers)
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
        (55001, {'activity_id': abyss[0]['activity_id'], 'is_back': False, 'history_max_layer': history_layer,
                 'last_version_max_unlock_layer': min(len(layers), history_layer + 1) if initial else len(layers), 'refresh_timestamp': refresh,
                 'layer_info_list': layers, 'got_layer_reward_list': user.get('abyss_rewards', [])}),
        (75009, {'now_cycle': c['core'][0]['cycle'], 'next_cycle': c['core'][0]['cycle'],
                 'refresh_timestamp': period_window('core')[1], 'max_score_info': {},
                 'stage_info': [{'id': x['id'], 'sign': 1, 'min_time': progress(user,67,x['id'])['best_time']*1000}
                                for x in c['core'] if progress(user,67,x['id']).get('clear_times')]}),
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
