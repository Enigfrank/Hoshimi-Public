"""决斗王对局属性、配置队伍、战斗动作及逐回合结算。"""

from copy import deepcopy
import time

from resident_catalog import resident_config, resident_rows
from gameplay_protocol import read_client_data
from battle_rewards import grant_rewards


def chess_setting(name: str):
    """读取本机决斗王常量，不把生命与商店属性混成一个键空间。"""
    return read_client_data(f"require('GameSetting').{name}.value")[0]


def chess_stat(chess: dict, key: int) -> int:
    """取棋子的攻击、生命或经验键。"""
    return next((row['value'] for row in chess['chess_attr_list'] if row['key'] == key), 0)


def chess_set_stat(chess: dict, key: int, value: int) -> None:
    """修改棋子持久属性，战斗临时生命只修改快照。"""
    row = next((row for row in chess['chess_attr_list'] if row['key'] == key), None)
    if row is None:
        row = {'key': key, 'value': 0}
        chess['chess_attr_list'].append(row)
    row['value'] = value


def repair_round_state(game: dict) -> None:
    """迁移旧局共用键，保留金币和编队，生命只根据已保存胜负恢复。"""
    game['attrs'] = {int(k): v for k, v in game.get('attrs', {}).items()}
    cfg = resident_config('AutoChessStageCfg', game['stage_id'])
    game.setdefault('wins', 0)
    game.setdefault('losses', 0)
    game.setdefault('round', 1)
    game.setdefault('next_battle_uid', 1)
    game.setdefault('restart_times', 0)
    hp_limit=chess_setting('auto_chess_lose_limit') if game['game_type']==1 else cfg['defeat_num']
    game.setdefault('player', {'hp': max(0, hp_limit - game['losses']),
        'wins': game['wins'], 'round': game['round'], 'stage': cfg['id'],
        'boss': 0, 'battle_uid': game['next_battle_uid'], 'sunglass': 0})
    for key, value in {4: chess_setting('auto_chess_prepare_grid_num'),
                      5: chess_setting('auto_chess_battle_grid_num'), 6: 3, 7: 0, 8: 0}.items():
        game['attrs'][key] = value
    game.setdefault('last_settled_round', 0)


def player_base(game: dict) -> list:
    """构造USER_INFO_KEY，值10使用value2传输战斗编号。"""
    p = game['player']
    # 原始InitBaseData在胜场字段触发Boss判断，必须先恢复回合、关卡和Boss标记。
    return [{'key': 1, 'value': p['hp']}, {'key': 4, 'value': p['round']},
        {'key': 5, 'value': p['stage']}, {'key': 6, 'value': p['boss']}, {'key': 2, 'value': p['wins']},
        {'key': 10, 'value': 0, 'value2': p['battle_uid']},
        {'key': 11, 'value': p['sunglass']}]


def shop_attributes(game: dict) -> list:
    """构造ATTR_KEY，金币、栏位与商店等级独立于玩家生命。"""
    return [{'key': key, 'value': value} for key, value in sorted(game['attrs'].items())]


def battle_enemy(game: dict) -> dict:
    """按真实PVE组和轮次生成对手，配置耗尽时沿用最后一轮而非空队伍。"""
    cfg = resident_config('AutoChessStageCfg', game['stage_id'])
    if game['game_type']==1:
        from resident_autochess_pvp import classic_enemy
        team=classic_enemy(game)
    else:
        teams = [r for r in resident_rows('AutoChessTeamCfg') if r['group_id'] == cfg['group_id'] and r['type'] == 1]
        if not teams:raise ValueError('决斗王关卡没有本地对手配置')
        if game['game_type'] == 0 and game['stage_id'] == chess_setting('auto_chess_boss_stage') and game['player']['wins'] >= 4:
            team = resident_config('AutoChessTeamCfg',chess_setting('auto_chess_boss_team_id'))
        else:
            regular = [r for r in teams if r['round'] != 99]
            team = next((r for r in regular if r['round'] == game['round']),max(regular,key=lambda r:r['round']))
    from resident_autochess import chess_row
    rows = []
    for index, source in enumerate(team['team_info'], 1):
        row = chess_row(900000 + index, source[0], index)
        chess_set_stat(row, 3, source[1])
        chess_set_stat(row, 1, chess_stat(row, 1) + source[2])
        chess_set_stat(row, 2, chess_stat(row, 2) + source[3])
        from resident_autochess_buffs import sync_chess_buffs
        sync_chess_buffs(row,game.get('exp_discount',0))
        extras = source[4]
        # 本机201首轮使用扁平[1,1]，其余同列为[[buff_id,参数]]。
        if extras and isinstance(extras[0],int):
            extras = [extras]
        for extra in extras:
            buff_id, values = extra
            effect = resident_config('AutoChessBuffCfg',buff_id)
            if effect['action_type'] == 1:
                # 通用加属性的标量按同值攻击／生命处理，保留为明确本地规则。
                amount = [values,values] if isinstance(values,int) else values
                for key,value in enumerate(amount,1):
                    chess_set_stat(row,key,chess_stat(row,key)+value)
            elif not any(b['buff_id'] == buff_id for b in row['buff_list']):
                from resident_autochess_buffs import buff_record
                row['buff_list'].append(buff_record(row['unique_id']*100+50+len(row['buff_list']),buff_id,row))
        rows.append(row)
    return {'base_info_list': [{'key': 1, 'value': team['hp_num'] if game['game_type']==1 else cfg['defeat_num']},
        {'key': 2, 'value': team['win_num'] if game['game_type']==1 else 0}, {'key': 4, 'value': game['round']}, {'key': 5, 'value': cfg['id']}],
        'chess_list': rows}


def launch_round(user: dict, game: dict) -> None:
    """只创建一次待确认战斗，重复读取不会增加胜利、扣生命或重发首通。"""
    if game.get('pending_round'):
        return
    if game.get('manager_upgrade'):
        raise ValueError('请先选择管理员强化')
    from resident_autochess_upgrade import manager_prepare_effects
    prepare_chess = deepcopy(game['chess_list'])
    manager_prepare_effects(game, 10)
    enemy = battle_enemy(game)
    from resident_autochess_effects import run_combat
    facts = {}
    result, actions, permanent = run_combat(game['chess_list'], enemy['chess_list'], game['attrs'][5], game.get('team_bonus'),game['round'],facts,game.get('catalog_bonuses'),game['game_type']==1)
    cfg = resident_config('AutoChessStageCfg', game['stage_id'])
    wins = game['player']['wins'] + int(result == 1)
    from resident_autochess_pvp import player_damage
    lost=player_damage(enemy['chess_list']) if game['game_type']==1 else 1
    hp = max(0,game['player']['hp']-lost) if result==2 else game['player']['hp']
    limit=chess_setting('auto_chess_win_limit') if game['game_type']==1 else cfg['win_num']
    outcome = 4 if wins >= limit else 5 if hp <= 0 else 3
    replay = {'game_type': game['game_type'], 'result': result,
        'battle_uid': game['next_battle_uid'], 'user_id1': user['uid'], 'user_id2': 0,
        'auto_chessboard_info1': {'base_info_list': player_base(game),
            'chess_list': deepcopy(game['chess_list'])},
        'auto_chessboard_info2': enemy, 'max_group_id': 1, 'point': 0, 'point_detail': [],
        'enemy_name': '本地训练对手' if game['game_type']==1 else cfg['enemy_name'], 'enemy_icon':1084 if game['game_type']==1 else int(cfg['enemy_icon'])}
    if game['game_type']==1:
        dealt=player_damage(game['chess_list']) if result==1 else 0
        enemy_hp=next(r['value'] for r in enemy['base_info_list'] if r['key']==1)
        facts['player_damage']={'dealt':dealt,'received':lost if result==2 else 0,'enemy_hp_after':max(0,enemy_hp-dealt)}
        if outcome in (4,5):
            from resident_autochess_pvp import classic_score
            projected=deepcopy(game);projected['player'].update(wins=wins,hp=hp)
            projected['losses']+=int(result==2)
            replay['point'],replay['point_detail']=classic_score(projected)
    game['pending_round'] = {'round': game['round'], 'wins': wins, 'hp': hp, 'state': outcome,
        'replay': replay, 'actions': actions, 'prepare_chess': prepare_chess, 'permanent': permanent,'facts':facts}
    game['next_battle_uid'] += 1


def round_messages(game: dict, historical: bool = False) -> list:
    """按先初始化战斗再下发动作的顺序发送完整动作组，组号不使用对局回合号。"""
    round_data = game.get('last_battle') if historical else game.get('pending_round')
    if not round_data:
        raise ValueError('决斗王尚无可用战斗记录')
    return [(89113, {'game_type': game['game_type'], 'battle_uid': round_data['replay']['battle_uid'],
        'battle_info': {'group_id': 1, 'action_list': deepcopy(round_data['actions'])}})]


def confirm_round(user: dict, state: dict, game: dict) -> list:
    """确认当前战报后推进一轮；终局首通、奖励和历史只保存一次。"""
    pending = game.get('pending_round')
    if not pending:
        if game.get('last_settled_round') and 'last_settle_pushes' in game:
            return [(cmd,deepcopy(value)) for cmd,value in game.get('last_settle_pushes',[]) if cmd!=89211]
        raise ValueError('决斗王没有待结算战斗')
    if game['state'] not in (3, 4, 5):
        raise ValueError('决斗王尚未读取战报')
    game['player'].update(wins=pending['wins'], hp=pending['hp'])
    used = game.pop('battle_props', [])
    for row in game['chess_list']:
        row['buff_list'] = [b for b in row['buff_list'] if b['buff_id'] not in used]
    game['global_buffs'] = [b for b in game.get('global_buffs', []) if b['buff_id'] not in used]
    for row in game['chess_list']:
        amount = pending.get('permanent', {}).get(str(row['unique_id']), [0, 0])
        chess_set_stat(row, 1, chess_stat(row, 1)+amount[0])
        chess_set_stat(row, 2, chess_stat(row, 2)+amount[1])
    removed = pending.get('facts',{}).get('removed',[[],[]])[0]
    game['chess_list'] = [r for r in game['chess_list'] if r['unique_id'] not in removed]
    from resident_autochess_global import confirm_catalog_growth
    confirm_catalog_growth(game,pending.get('facts',{}).get('catalog_changes',[{},{}])[0])
    from resident_autochess_transactions import clear_prepare_temporary
    clear_prepare_temporary(game)
    game['wins'], game['losses'] = pending['wins'], game['losses'] + int(pending['replay']['result'] == 2)
    game['last_settled_round'] = pending['round']
    game['last_battle'] = deepcopy(pending)
    game['replay'] = deepcopy(pending['replay'])
    earned=[];proof=None
    if game['game_type']==1:
        from resident_autochess_pvp import confirm_medal_facts
        earned=confirm_medal_facts(state,game,pending.get('facts',{}).get('metrics',[{},{}])[0])
        if earned:
            from resident_autochess_pvp import classic_record
            outcome=4 if pending['replay']['result']==1 else 5 if pending['replay']['result']==2 else 3
            proof=classic_record(game,outcome);proof['medal_ids']=earned
            state.setdefault('medal_records',[]).append(deepcopy(proof))
    game.pop('pending_round')
    from resident_autochess import prepare_data, stage_data, refill_shop
    if pending['state'] in (4, 5):
        cfg = resident_config('AutoChessStageCfg', game['stage_id'])
        if pending['state'] == 4 and game['game_type'] == 0:
            stages = state.setdefault('stage_list', [])
            if not any(r['id'] == cfg['id'] for r in stages):
                stages.append({'id': cfg['id'], 'time': int(time.time())})
                for saved_game in state['games'].values():
                    saved_game['cleared_stages'] = [r['id'] for r in stages]
                if cfg['drop_item']:
                    grant_rewards(user, [{'id': cfg['drop_item'][0], 'num': cfg['drop_item'][1]}])
        state['records'].append({'time': int(time.time()), 'auto_chessboard_info': {
            'base_info_list': player_base(game), 'chess_list': deepcopy(game['chess_list']), 'card_ids': []},
            'rank': 1 if pending['state'] == 4 else 2, 'rank_score': 0, 'new_rank_score': 0,
            'point': 0, 'point_detail': []})
        if game['game_type']==1:
            from resident_autochess_pvp import classic_record
            record=classic_record(game,pending['state'])
            state.setdefault('classic_records',[]).append(record)
            state['records'][-1].update(point=record['point'],point_detail=deepcopy(record['point_detail']))
        game['state'] = 0
        pushes = [(89201, stage_data(state)), (89125, {'game_type': game['game_type'], 'state': 0})]
        if game['game_type']==1:
            pushes.append((89211,{'type':1,'record':[record]}))
    else:
        game['round'] += 1
        game['player'].update(round=game['round'], battle_uid=game['next_battle_uid'])
        if game['game_type'] == 0 and game['stage_id'] == chess_setting('auto_chess_boss_stage') and game['player']['wins'] >= 4:
            game['player']['boss'] = 1
        game['attrs'][1] = game.get('coin_reset', 10)+game.pop('next_round_gold', 0)
        game['state'] = 1
        from resident_autochess_upgrade import offer_manager_upgrade, manager_prepare_effects
        offer_manager_upgrade(game)
        manager_prepare_effects(game, 9)
        refill_shop(game)
        pushes = [(89101, prepare_data(game)), (89125, {'game_type': game['game_type'], 'state': 1})]
    if earned:
        pushes.append((89205,{'medal':deepcopy(state['medal'])}))
        pushes.append((89211,{'type':2,'record':[proof]}))
    game['last_settle_pushes'] = deepcopy(pushes)
    return pushes
