"""本地确定的3／5／7回合商店升级与管理员强化选择。"""

from resident_catalog import resident_config
from resident_autochess_round import chess_stat, chess_set_stat, chess_setting


def manager_chess(game: dict) -> dict:
    """按真实棋子类型寻找管理员，不依赖其当前站位。"""
    return next(r for r in game['chess_list'] if resident_config('AutoChessCfg', r['chess_id'])['type'] == 1)


def offer_manager_upgrade(game: dict) -> None:
    """第3／5／7回合提升商店并生成稳定选项，断线不会重新抽取技能。"""
    from resident_autochess_scene import scene_shop_level, grant_scene_reward
    level = scene_shop_level(game)
    previous = game['attrs'][11]
    game['attrs'][11] = level
    grant_scene_reward(game,previous)
    game.setdefault('admin_level', 1)
    if game['game_type'] == 0 and game['stage_id'] == chess_setting('auto_chess_boss_stage') and game['player']['wins'] >= 4 and not game.get('boss_upgrade_chosen'):
        game['manager_upgrade'] = {'old_level':4,'new_level':5,'type':1,'hero_upgrade_attrs':[],
            'hero_upgrade_buffs':[{'index':1,'buff_ids':[chess_setting('auto_chess_boss_manager_buff')]}]}
        return
    if game['admin_level'] >= level or game.get('manager_upgrade'):
        return
    cfg = resident_config('AutoChessCfg', manager_chess(game)['chess_id'])
    options = cfg['level_buffs'][3][game['admin_level']-1]
    game['manager_upgrade'] = {'old_level': game['admin_level'], 'new_level': game['admin_level']+1, 'type': 0,
        'hero_upgrade_attrs': [{'index': i, 'attr_list': [{'key': 1, 'value': row[0]},
            {'key': 2, 'value': row[1]}]} for i, row in enumerate(options[0][:cfg['level_buffs'][0]], 1)],
        'hero_upgrade_buffs': [{'index': i, 'buff_ids': row} for i, row in enumerate(options[1][:cfg['level_buffs'][1]], 1)]}


def apply_manager_effect(game: dict, owner: dict, buff_id: int, timing: int) -> None:
    """执行配置能确定的准备阶段加属性与金币；战斗内触发效果由战斗域继续补齐。"""
    cfg = resident_config('AutoChessBuffCfg', buff_id)
    if cfg['moment'] != timing:
        return
    from resident_autochess_transactions import prepare_effect
    prepare_effect(game,owner,cfg)


def manager_prepare_effects(game: dict, timing: int) -> None:
    """按真实已拥有技能处理准备时机，包含默认管理员和普通棋子的回合效果。"""
    from resident_autochess_transactions import prepare_event
    prepare_event(game,timing)


def choose_manager_upgrade(game: dict, request: dict) -> None:
    """校验当前两类选项，实际增加属性和技能，不扣除配置未要求的金币。"""
    pending = game.get('manager_upgrade')
    if not pending:
        raise ValueError('当前没有待选择的管理员强化')
    attr = next((r for r in pending['hero_upgrade_attrs'] if r['index'] == request['attr_index']), None)
    buff = next((r for r in pending['hero_upgrade_buffs'] if r['index'] == request['buff_index']), None)
    boss = pending['type'] == 1
    if (attr is None and not (boss and request['attr_index'] == 0)) or buff is None:
        raise ValueError('管理员强化选项无效')
    owner = manager_chess(game)
    for row in attr['attr_list'] if attr is not None else []:
        chess_set_stat(owner, row['key'], chess_stat(owner, row['key'])+row['value'])
    game.setdefault('next_buff_uid', 1000000)
    for buff_id in buff['buff_ids']:
        owner['buff_list'].append({'unique_id': game['next_buff_uid'], 'buff_id': buff_id,
            'owner_type': 0, 'source_type': 0, 'source_uid': owner['unique_id'],
            'source_cfg_id': owner['chess_id'], 'other_param':
                [{'key':3,'value':game['round']}] if resident_config('AutoChessBuffCfg',buff_id)['action_type'] == 38 else []})
        game['next_buff_uid'] += 1
        apply_manager_effect(game, owner, buff_id, 1)
    game['admin_level'] = pending['new_level']
    if boss:game['boss_upgrade_chosen'] = True
    game.pop('manager_upgrade')
