"""常驻玩法的专用初始化和进度恢复。"""

from copy import deepcopy

from currency_data import build_currency_balances
from resident_catalog import resident_rows
from resident_rogue import rogue_data


def resident_state(user: dict) -> dict:
    """建立账号内独立玩法状态，首次登录不预发通关或领奖记录。"""
    state = user.setdefault('resident', {})
    defaults = {
        'skuld': {'activity_id': 321201, 'client_opt': [], 'mission_list': [], 'point': 0, 'reward_list': []},
        'osiris': {'pass_stages': [], 'pass_chapters': []},
        'ash': {'pass_stages': []}, 'momotalk': [],
        'pub': {'stage_list': [], 'cook_stage_list': [], 'battle_stage_list': [], 'illustrated': [], 'time_state': 1},
        'music': {'challenge_info': []}, 'music_rewards': [],
        'pinball': {'hai_la_level': 1, 'stage_list': [], 'skills': [], 'select_active_skills': [], 'challenge_score': 0},
        'hodur': {'normal_stage_info': [], 'challenge_stage_info': []},
        'vehicle': {'stages': [], 'using_vehicle': resident_rows('MergeBallVehicleCfg')[0]['id'], 'unlock_buffs': []},
        'race': {'activity_id': 4343601, 'mission_list': [], 'tank_id': 0, 'tank_info': [],
                 'challenge_stage_info': {'round': 0, 'max_score': 0}},
        'point_rewards': [], 'scores': {}, 'world': {}, 'unlock_states': [],
        'autochess': {'stage_list': [], 'medal': []}, 'autochess_records': [],
    }
    for key, value in defaults.items():
        state.setdefault(key, value)
    from resident_race import initialize_race
    initialize_race(state['race'])
    from resident_combat import initialize_combat_state
    initialize_combat_state(state)
    from resident_pinball import initialize_pinball
    from resident_vehicle import initialize_vehicle
    initialize_pinball(user, state['pinball'])
    initialize_vehicle(state['vehicle'])
    return state


def resident_pushes(user: dict) -> list[tuple]:
    """登录下发已开放常驻的实际存档，避免页面访问未初始化的数据。"""
    from resident_world import world_data, world_sea_data
    from resident_board import board_history
    from resident_board_data import board_state
    from resident_card import card_push
    from resident_sidegames import side_pushes
    from resident_match import online_login_pushes
    from resident_race import race_data
    from resident_combat import combat_point_data
    from resident_pinball import pinball_data
    from resident_vehicle import vehicle_data
    from resident_music import music_data
    state = resident_state(user)
    from resident_autochess import stage_data
    state['skuld']['point'] = build_currency_balances(user).get(76, 0)
    state['skuld']['reward_list'] = [i for i in state['point_rewards']
        if any(r['id'] == i and r['activity_id'] == 321201 for r in resident_rows('ActivityPointRewardCfg'))]
    result = list(side_pushes(user)) + [(85035, {'chapter_id': board_state(user)['chapter_id']}), (51001, board_history(user)),
        (90051, card_push(user)),
        (28801, world_data(user)), (86035, world_sea_data(user)), (24051, state['skuld']), (68171, state['osiris']), (68181, state['ash']),
        (12119, {'momotalk_list': state['momotalk']}), (89011, state['pub']), (61047, music_data(state['music'])),
        (89401, pinball_data(state['pinball'])), (89421, state['hodur']), (89701, vehicle_data(state['vehicle'])),
        (84331, race_data(state['race'])), (89601, rogue_data(user)),
        (89201, stage_data(state['autochess'])),
        (60105, {'score_info_list': [{'key': int(k), 'score': v} for k, v in state['scores'].items()]}),
        (60107, {'reward_info_list': state['point_rewards']}),
        (85101, {'reward_list': state['point_rewards']}),
        (85105, {'opt_list': [{'id': r['id'], 'opt': [{'key': int(k), 'value': v} for k, v in r['opts'].items()]}
                             for r in state['unlock_states']]})]
    result.extend(online_login_pushes(user))
    from resident_autochess_pvp import classic_history
    result.append((89207,classic_history(state['autochess'])))
    result.extend((60201, combat_point_data(user, activity)) for activity in (323801, 3639701))
    for game_type in range(4):
        result.extend([(c, v) for c, v in __import__('resident_autochess').autochess_pushes(user, game_type) if c not in (90080, 89201)])
    for cfg in resident_rows('SandPlayMapCfg'):
        if cfg['activity_id'] == 323641:
            result.append((79601, {'activity_id': cfg['activity_id'],
                'stage_list': list(state['world'].get(str(cfg['activity_id']), {}).values())}))
    return [(c, deepcopy(v)) for c, v in result]
