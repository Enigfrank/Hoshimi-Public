"""单人战斗队伍、会话和结算数据。"""

import random
import time
from copy import deepcopy
from functools import lru_cache
from itertools import count

from .gameplay_protocol import decode_message, read_client_data
from .proto_bridge import get_bridge
from .currency_data import STAMINA_CURRENCY_ID
from .battle_rewards import drop_config, roll_drops, grant_rewards
from .hero_preferences import hero_info_data
from .battle_rewards import equipment_data
from .battle_rewards import spend_items
from .task_progress import update_task_progress
from .battle_heroes import owned_battle_hero, trial_battle_hero
from .loadout_storage import servant_data
from .resident_battle import RESIDENT_STAGE_CONFIGS, complete_resident_battle

_BATTLE_IDS = count(int(time.time()))

_DYNAMIC_ATTRIBUTE_HERO = 1085
_NORMAL_ATTACK_ATTRIBUTES = frozenset((0, 2, 4, 6, 8, 10, 12, 14))


@lru_cache(maxsize=256)
def _hero_attack_attribute(hero_id: int) -> int:
    """读取角色基础攻击属性，动态属性由战斗队伍另行解析。"""
    if hero_id <= 0:
        return -1
    value = read_client_data(
        f"(function() local h=require('HeroCfg')[{int(hero_id)}]; local a=h and h.ATK_attribute; "
        "return type(a)=='table' and tonumber(a[1]) or -1 end)()"
    )
    return int(value)


def _resolve_battle_hero_id(selected_id: int, hero_type: int) -> int:
    """把试用模板编号解析为 HeroCfg 中的实际角色编号。"""
    if hero_type != 2:
        return int(selected_id)
    value = read_client_data(
        f"((require('HeroStandardSystemCfg')[{int(selected_id)}] or {{}}).hero_id or 0)"
    )
    return int(value)


def _apply_dynamic_main_damage_type(team: list[dict], hero_ids: list[int]) -> None:
    """按两名队友的属性等概率选择诗寇蒂的主伤害属性。"""
    if len(hero_ids) != 3 or hero_ids.count(_DYNAMIC_ATTRIBUTE_HERO) != 1:
        return
    teammate_attributes = [
        _hero_attack_attribute(hero_id)
        for hero_id in hero_ids
        if hero_id != _DYNAMIC_ATTRIBUTE_HERO
    ]
    if len(teammate_attributes) != 2 or any(
            attribute not in _NORMAL_ATTACK_ATTRIBUTES for attribute in teammate_attributes):
        return
    dynamic_attribute = random.choice(teammate_attributes)
    for member, hero_id in zip(team, hero_ids):
        if hero_id == _DYNAMIC_ATTRIBUTE_HERO:
            member['main_damage_type'] = dynamic_attribute


STAGE_CONFIGS = {
    **RESIDENT_STAGE_CONFIGS,
    1: 'BattleChapterStageCfg', 601: 'BattleActivityStoryStageCfg', 2: 'BattleDailyStageCfg',
    7: 'BattleTowerStageCfg', 8: 'BattleEnchantmentStageCfg', 9: 'BattleEquipStageCfg',
    10: 'BattleBossStageCfg', 100: 'BattleBossStageCfg', 11: 'BattleMythicStageCfg',
    12: 'BattleBaseTeachStageCfg', 13: 'BattleHeroTeachStageCfg',
    20: 'BattleHeroTrialStageCfg', 76: 'BattleResidentHeroTrialStageCfg',
    35: 'BattleMythicFinalCfg', 53: 'BattleEquipSeizureStageCfg',
    40: 'BattleEquipBreakThroughMaterialStageCfg', 1003: 'BattleAbyssCfg',
    1006: 'BattleAdvanceTestStageCfg', 52: 'BattlePolyhedronStageCfg',
    78: 'BattleRogueTeamStageCfg', 57: 'BattleHeartDemonStageCfg',
    1002: 'BattleDamageTestCfg', 67: 'BattleCoreVerificationCfg',
    15: 'BattleChessStageCfg',
    1009: 'BattleActivityAdvanceMonsterTestCfg',
    82: 'BattleMultiHeartDemonCfg',
    93: 'BattleCoreVerificationCl4Cfg',
    61: 'BattleActivityRouletteCfg',
}


def battle_cost(config: dict) -> dict:
    """把关卡整数成本或物品列表成本统一为物品 ID 与数量。"""
    cost = config.get('cost', 0) or 0
    if isinstance(cost, int):
        return {'id': 4, 'num': cost}
    if isinstance(cost, (list, tuple)) and len(cost) >= 2:
        return {'id': int(cost[0]), 'num': int(cost[1])}
    raise ValueError('关卡成本配置无效')


def chapter_stage_cleared(user: dict, stage_id: int) -> bool:
    """判断主线剧情关卡是否已有真实通关记录。"""
    return int(user.get('battle_progress', {}).get(f'1:{stage_id}', {}).get('clear_times', 0)) > 0


def battle_cost_for(user: dict, stage_type: int, stage_id: int, config: dict) -> dict:
    """按玩法计算本次消耗；主线剧情关卡首通前消耗配置体力，通关后为 0。"""
    cost = battle_cost(config)
    if stage_type == 1 and (user.get('account_template') != 'normal' or chapter_stage_cleared(user, stage_id)):
        return {'id': cost['id'], 'num': 0}
    return cost


def battle_exp_reward(config: dict, cost: dict, times: int) -> int:
    """管理员经验优先取关卡配置 user_exp，缺失时按本次实际消耗的体力等量折算；未付费进入不给经验。"""
    if cost['num'] <= 0:
        return 0
    configured = int(config.get('user_exp') or 0)
    if configured > 0:
        return configured * times
    return cost['num'] * times if cost['id'] == STAMINA_CURRENCY_ID else 0


@lru_cache(maxsize=512)
def stage_config(stage_type: int, stage_id: int) -> dict:
    """按实际玩法校验关卡配置，拒绝不存在的关卡。"""
    name = STAGE_CONFIGS.get(stage_type)
    if name is None:
        return {}
    if stage_type in (602, 87, 84, 71):
        from .resident_battle import resident_stage_id
        stage_id = resident_stage_id(stage_type, stage_id)
    if stage_type == 11:
        from .challenge_data import challenge_catalog, mythic_partition_stage
        catalog = challenge_catalog()
        for difficulty in catalog['mythic']:
            partitions = [difficulty['main_partition']] + difficulty['sub_partition_list']
            if stage_id in partitions:
                actual = mythic_partition_stage(stage_id)
                return read_client_data(f"require('{name}')[{actual}]")
        return {}
    if stage_type in (67, 1006):
        info = 'CoreVerificationInfoCfg' if stage_type == 67 else 'AdvanceTestCfg'
        return read_client_data(f"(function() local c=require('{info}')[{int(stage_id)}]; "
                                f"return c and require('{name}')[c.stage_id] or {{}} end)()")
    return read_client_data(f"require('{name}')[{int(stage_id)}] or {{}}")


def start_battle(user: dict, request: dict, max_times: int = 5) -> tuple[dict, dict]:
    """校验当前关卡、深阱角色占用及所选队伍；扫荡可放宽倍率上限。"""
    common = deepcopy(request['common_info'])
    if common['type'] == 1003:
        from .abyss import prepare_abyss_battle
        abyss = prepare_abyss_battle(user, common)
    if common['type'] == 8:
        from .challenge_data import validate_enchant_stage
        validate_enchant_stage(user, common['dest'])
    if common['type'] == 35:
        from .challenge_data import challenge_catalog, mythic_final_teams
        difficulty = user.get('mythic_final_difficulty', 1)
        final = next((c for c in challenge_catalog()['final'] if c['id'] == difficulty), None)
        teams = mythic_final_teams(final) if final else []
        index = common['dest']
        # 1-19 级为单队直达选人，客户端可能下发 0、难度号或关卡号而不是队伍 1。
        if len(teams) == 1 and index in (0, difficulty, teams[0][0]):
            index = 1
        if final is None or not 1 <= index <= len(teams):
            raise ValueError('终末黑区队伍序号无效')
        stages = teams[index-1]
        cleared = user.get('mythic_final_runs', {}).get(str(difficulty), {}).get(str(index), {}).get('stages', [])
        stage_id = next((s for s in stages if s not in cleared), None)
        if stage_id is None:
            raise ValueError('该队伍路线已通关，请重置后重试')
        cfg = stage_config(35, stage_id)
    else:
        actual_dest = common['dest']
        if common['type'] == 78:
            from .rogue_team import prepare_rogue_battle
            actual_dest = prepare_rogue_battle(user, common)
        if common['type'] == 74 and actual_dest == 0:
            from .resident_board_data import board_run, board_entity
            from .resident_catalog import resident_config
            chance = board_entity(board_run(user))['chance']
            if not chance['chance_id'] or not chance['is_unlock']:
                raise ValueError('节奏棋盘机会尚未开启')
            c = resident_config('ChessBoardChanceCfg', chance['chance_id'])
            if c['type'] != 8:
                raise ValueError('当前机会不是节奏玩法')
            actual_dest = c['params'][0]
        if common['type'] == 61:
            from .activity_pt_scroll import validate_activity_pt_scroll_stage
            activity_stage = validate_activity_pt_scroll_stage(user, common)
            cfg = deepcopy(stage_config(common['type'], actual_dest))
            cfg['cost'] = deepcopy(activity_stage['cost'])
        else:
            cfg = stage_config(common['type'], actual_dest)
    if not cfg:
        raise ValueError('关卡不存在或尚未实现对应玩法')
    times = common.get('battle_times') or 1
    if not 1 <= times <= max_times:
        raise ValueError('战斗倍率无效')
    cost = battle_cost_for(user, common['type'], common['dest'], cfg)
    amount = cost['num'] * times
    if amount:
        from .currency_data import build_currency_balances
        balances = build_currency_balances(user)
        available = balances.get(cost['id'], user.get('materials', {}).get(str(cost['id']), 0))
        if available < amount:
            raise ValueError('体力或轮盘票不足')
    if common['type'] == 40:
        from .equip_exploration import available_node
        available_node(user, common.get('index', 0))
        choices = user['equip_exploration']['map']['choice_stage_list']
        if not any(n['id'] == common['index'] and n['stage'] == common['dest'] for n in choices):
            raise ValueError('神域解析关卡不属于当前节点')
    if common['type'] == 52:
        from .polyhedron_heroes import prepare_battle
        polyhedron_team = prepare_battle(user, common)
    if common['type'] == 82:
        from .mult_heart_demon import validate_demon_battle
        validate_demon_battle(user, common)
    if common['type'] == 93:
        from .neural_matrix import prepare_neural_battle
        neural = prepare_neural_battle(user, request)
    if common['type'] == 87:
        from .resident_pub import validate_pub_stage
        validate_pub_stage(user, cfg['id'])
    if common['type'] in (86, 89):
        from .resident_combat import validate_combat_stage
        validate_combat_stage(user, common, cfg['id'])
    if common['type'] == 92:
        from .resident_hodur_battle import prepare_hodur_battle
        hodur = prepare_hodur_battle(user, request, cfg['id'])
    if common['type'] == 603:
        from .resident_pinball import validate_pinball_battle
        validate_pinball_battle(user, common, cfg['id'])
    if common['type'] == 84:
        from .resident_sea import validate_sea_battle
        validate_sea_battle(user, common, cfg['id'])
    if common['type'] == 602:
        from .resident_skuld import validate_skuld_stage
        validate_skuld_stage(user, cfg['id'])
        if cfg.get('tag') == 2:
            raise ValueError('溯梦剧情必须使用剧情观看协议')
    if common['type'] == 1:
        from .chapter_maps import validate_chapter_stage
        validate_chapter_stage(user, common['dest'])
    if common['type'] in (20, 76):
        from .hero_trial import prepare_hero_trial_battle
        prepare_hero_trial_battle(common, cfg)
    bridge = get_bridge()
    heroes = hero_info_data(user)['hero_info_list']
    equipment = equipment_data(user)['equip_list']
    by_id = {h['hero_base_info']['id']: h for h in heroes}
    servants = servant_data(user)['servant_list']
    team = polyhedron_team if common['type'] == 52 else []
    for selection in ([] if common['type'] == 52 else common['hero_list']):
        hero_id = selection['hero_id']
        if not hero_id:
            continue
        hero_type = selection['hero_type']
        if hero_type == 2:
            member = trial_battle_hero(hero_id)
            if not member:
                raise ValueError('试用角色不存在')
        elif hero_type == 1 and hero_id in by_id:
            member = owned_battle_hero(by_id[hero_id], equipment, servants)
        elif common['type'] in (71, 72, 73, 74, 75) and hero_type == 1 and hero_id in {
                h[1] for h in cfg.get('hero_list', []) if h[0]}:
            member = trial_battle_hero(hero_id)
            if not member:
                raise ValueError('玩法试用模板不存在')
        else:
            raise ValueError('角色未拥有')
        team.append(member)
    if not team:
        raise ValueError('战斗队伍为空')
    if len(team) == 3:
        hero_ids = [_resolve_battle_hero_id(member['hero_base_info']['id'], member['hero_type'])
                    for member in team]
        _apply_dynamic_main_damage_type(team, hero_ids)
    now = int(time.time())
    battle_id = max(next(_BATTLE_IDS), user.get('last_battle_id', 0)+1)
    user['last_battle_id'] = battle_id
    battle = {'id': battle_id, 'started': now, 'common': common, 'config': cfg,
              'reported': None, 'settlement': None, 'max_times': max_times}
    if common['type'] == 1003:
        battle['abyss'] = abyss
    if common['type'] == 78:
        battle['rogue_serial'] = user['rogue_team']['run']['serial']
    if common['type'] == 92:
        battle['hodur'] = hodur
    if common['type'] == 93:
        battle['neural'] = neural
    if common['type'] == 35:
        battle['final_difficulty'] = difficulty
        battle['final_team_index'] = index
        battle['final_reset'] = user.get('mythic_final_resets', {}).get(str(difficulty), 0)
    if common['type'] == 82:
        run = validate_demon_battle(user, common)
        battle['demon_serial'] = run['serial']
        run['battle_id'] = battle_id
        run['pending'] = None
    player = {'player_id': user['uid'], 'player_battle_info': {
        'channel': 1, 'server': 1, 'hero_list': team, 'nick': user.get('nick', 'Developer'),
        'level': user.get('level', 80), 'icon': user.get('icon', 1084), 'frame': user.get('icon_frame', 2001)},
        'player_room_info': {'is_master': 1, 'is_ready': 1}}
    return battle, {'player_info': player}


def start_story(request: dict, user: dict | None = None) -> dict:
    """剧情观看使用独立完成协议，拒绝将战斗关卡当作剧情通关。"""
    kind = 601 if request.get('activity_id') else 1
    cfg = stage_config(kind, request['stage_id'])
    if not cfg:
        for candidate in RESIDENT_STAGE_CONFIGS:
            cfg = stage_config(candidate, request['stage_id'])
            if cfg:
                kind = candidate
                break
    if not cfg or cfg.get('tag') != 2:
        raise ValueError('请求的关卡不是剧情节点')
    if user is not None and kind == 1:
        from .chapter_maps import validate_chapter_stage
        validate_chapter_stage(user, request['stage_id'])
    if user is not None and kind == 87:
        from .resident_pub import validate_pub_stage
        validate_pub_stage(user, cfg['id'])
    if user is not None and kind == 602:
        from .resident_skuld import validate_skuld_stage
        validate_skuld_stage(user, cfg['id'])
    return {'id': next(_BATTLE_IDS), 'started': int(time.time()),
            'common': {'type': kind, 'dest': request['stage_id'], 'activity_id': request.get('activity_id', 0),
                       'hero_list': [], 'battle_times': 1}, 'config': cfg,
            'reported': {'result': 1, 'info': {}}, 'settlement': None}


def settle_battle(user: dict, battle: dict, battle_id: int) -> dict:
    """结算本次通关次数与实际战果；重复结算返回存档，失败不计通关。"""
    if not battle or battle['id'] != battle_id:
        raise ValueError('战斗 ID 不匹配')
    if battle.get('period_expired'):
        raise ValueError('玩法周期已刷新，请重新进入战斗')
    if battle['common']['type'] == 1003:
        from .abyss import validate_abyss_settlement
        validate_abyss_settlement(user, battle)
    if battle['common']['type'] == 78:
        from .rogue_team import validate_rogue_battle
        validate_rogue_battle(user, battle)
    if battle['common']['type'] == 93:
        from .neural_matrix import validate_neural_settlement
        validate_neural_settlement(user, battle)
    if battle['common']['type'] == 35:
        difficulty = str(battle['final_difficulty'])
        if battle.get('final_reset', 0) != user.get('mythic_final_resets', {}).get(difficulty, 0):
            raise ValueError('终末黑区队伍已重置，请重新开战')
    if battle['settlement'] is not None:
        return battle['settlement']
    report = battle['reported']
    if (report is None and battle['common']['type'] == 52
            and int(battle['common'].get('dest') or 0) == 3035199):
        # 1-1 的 3035199 是没有敌人的整备房间，54032 可能不带战斗战果。
        report = {'result': 1, 'info': {}}
        battle['reported'] = report
    if report is None:
        raise ValueError('尚未收到战斗结果')
    result = min(report['result'], 3)
    info = report['info']
    common = battle['common']
    if common['type'] == 93:
        from .neural_matrix import settle_neural_battle
        result = settle_neural_battle(user, battle)
    win = result == 1
    if common['type'] == 8 and win:
        from .challenge_data import validate_enchant_stage
        from .server_clock import business_day
        if business_day(battle['started']) != business_day(int(time.time())):
            raise ValueError('联合特勤任务已跨日，请重新进入战斗')
        validate_enchant_stage(user, common['dest'])
    if common['type'] == 92:
        from .resident_hodur import hodur_serial
        if not battle.get('hodur'):
            raise ValueError('谧光旧战斗缺少路线信息，请重新进入关卡')
        if battle['hodur']['serial'] != hodur_serial(user, battle['hodur']['chapter']):
            raise ValueError('谧光路线已重置，本场战斗不能结算')
    seconds = info['2'] // 1000 if '2' in info else max(0, int(time.time()) - battle['started'])
    if common['type'] == 78:
        from .rogue_team import complete_rogue_battle
        complete_rogue_battle(user, battle, seconds)
    if common['type'] == 82:
        from .mult_heart_demon import settle_demon_battle
        settle_demon_battle(user, battle)
    stars = []
    for index, condition in enumerate(battle['config'].get('three_star_need') or [], 1):
        kind = condition[0]
        need = condition[1] if len(condition) > 1 else 1
        value = 0
        achieved = False
        if kind in (1, 3, 4, 9, 10, 14):
            field = {1: '3', 3: '4', 4: '2', 9: '5', 10: '6', 14: '9'}[kind]
            value = seconds if kind == 4 else info.get(field, 0)
            achieved = value <= need
        elif kind in (2, 13):
            value = int(info.get('3' if kind == 2 else '9', 0) == 0)
            achieved = value == 1
        elif kind == 8:
            value = int(win)
            achieved = win
        elif kind == 11:
            value = info.get('7', 0)
            achieved = value >= need
        elif kind == 12:
            value = int(any(h['hero_id'] == need for h in common['hero_list']))
            need = 1
            achieved = value == 1
        elif kind in (15, 16):
            items = dict(zip(info.get('12', []), info.get('13', [])))
            item_id = condition[2] if kind == 15 else need
            if kind == 16:
                need = 1
            value = items.get(item_id, 0)
            achieved = value >= need
        stars.append({'star_id': index, 'now_progress': value, 'need_progress': need,
                      'is_achieve': int(win and achieved)})
    progress_dest = (battle['config']['id'] if common['type'] == 35 or (common['type'] == 74 and common['dest'] == 0)
                     else common['dest'])
    key = f"{common['type']}:{progress_dest}"
    progress = user.setdefault('battle_progress', {}).setdefault(key, {'clear_times': 0, 'stars': []})
    times = max(1, min(common.get('battle_times') or 1, battle.get('max_times', 5)))
    if win:
        cost = battle_cost_for(user, common['type'], progress_dest, battle['config'])
        exp = battle_exp_reward(battle['config'], cost, times)
        cost['num'] *= times
        if cost['num']:
            spend_items(user, [cost])
            if cost['id'] == STAMINA_CURRENCY_ID:
                update_task_progress(user, 350, cost['num'], STAMINA_CURRENCY_ID)
        if exp:
            from .account_defaults import add_player_exp
            add_player_exp(user, exp)
        update_task_progress(user, 402, times)
        if common['type'] == 11:
            update_task_progress(user, 453, times)
        elif common['type'] in (10, 100):
            update_task_progress(user, 405, times, 301)
        progress['clear_times'] += times
        progress['stars'] = sorted(set(progress['stars']) | {s['star_id'] for s in stars if s['is_achieve']})
        progress['best_time'] = min(progress.get('best_time', seconds), seconds)
        progress['last_heroes'] = [h['hero_id'] for h in common['hero_list'] if h['hero_id']]
        from .hero_training_progress import complete_training_battle
        complete_training_battle(user, battle, times)
        if common['type'] == 1:
            from .chapter_maps import complete_chapter_stage
            complete_chapter_stage(user, common['dest'])
        battle['resident_rewards'] = complete_resident_battle(user, battle, seconds, info)
        if common['type'] == 35:
            from .challenge_data import challenge_catalog, mythic_final_teams
            difficulty = battle['final_difficulty']
            final = next(c for c in challenge_catalog()['final'] if c['id'] == difficulty)
            teams = mythic_final_teams(final)
            team_index = battle.get('final_team_index', common['dest'])
            stage_id = battle['config']['id']
            if not 1 <= team_index <= len(teams) or stage_id not in teams[team_index - 1]:
                raise ValueError('终末黑区路线已变更，请重新开战')
            runs = user.setdefault('mythic_final_runs', {}).setdefault(str(difficulty), {})
            team = runs.setdefault(str(team_index), {'stages': [], 'use_time': 0})
            if stage_id not in team['stages']:
                team['stages'].append(stage_id)
                team['use_time'] += seconds
            if all(all(s in runs.get(str(i), {}).get('stages', []) for s in stages)
                   for i, stages in enumerate(teams, 1)):
                cleared = user.setdefault('mythic_final_cleared', [])
                if difficulty not in cleared:
                    cleared.append(difficulty)
        if common['type'] == 40:
            from .equip_exploration import complete_node, update_health
            update_health(user, info.get('characters', []))
            complete_node(user, common['index'])
    resident_rewards = battle.get('resident_rewards', [])
    drop_runs = []
    if win and common['type'] != 89:
        drop_table = drop_config(battle['config'].get('drop_lib_id', 0))
        drop_runs = [roll_drops(drop_table, 1) for _ in range(times)]
    gains = [gain for run in drop_runs for gain in run] + resident_rewards
    if gains:
        grant_rewards(user, gains)
    if resident_rewards:
        if drop_runs:
            drop_runs[0].extend(resident_rewards)
        else:
            drop_runs = [list(resident_rewards)]
    if common['type'] == 52:
        from .polyhedron_game import settle_polyhedron
        settle_polyhedron(user, battle)
    if common['type'] == 1003:
        from .abyss import settle_abyss_battle
        settle_abyss_battle(user, battle, seconds)
    if common['type'] == 8 and win:
        from .challenge_data import refresh_enchant_slot
        refresh_enchant_slot(user, common['dest'])
    reported_heroes = [int(row.get('1', 0)) for row in info.get('characters', [])
                       if int(row.get('1', 0) or 0)]
    if common['type'] == 2 and len(reported_heroes) == 3:
        # 类型 2 的三人队伍主控/队长在客户端队伍中间；其它模式保持原顺序，
        # 避免特殊预设队伍（主控不在中间）被误重排。
        reported_heroes = [reported_heroes[1], reported_heroes[0], reported_heroes[2]]
    battle['settlement'] = {'result': 0, 'battle_result': {
        'battle_id': battle_id, 'result': result, 'dest': common['dest'], 'timestamp': int(time.time()),
        'clear_times': times if win else 0, 'target_times': times, 'use_seconds': seconds,
        'star_list': stars,
        'all_drop_list': ([{'battle_times': index, 'gain_list': run}
                           for index, run in enumerate(drop_runs, 1)]
                          if drop_runs else [{'battle_times': times, 'gain_list': []}]),
        'hero_id_list': reported_heroes or [h['hero_id'] for h in common['hero_list'] if h['hero_id']]}}
    return battle['settlement']
