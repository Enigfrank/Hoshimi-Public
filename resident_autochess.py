"""两版决斗王的自走棋准备、商店、编队、回放与持久化。"""

from copy import deepcopy
import time

from resident_catalog import resident_config, resident_rows
from resident_autochess_round import (repair_round_state, player_base, shop_attributes,
    chess_stat, launch_round, round_messages, confirm_round, battle_enemy, chess_setting)
from resident_autochess_prepare import buy_all_chess, retry_failed_round, purchase_chess, merge_chess
from resident_autochess_upgrade import choose_manager_upgrade
from resident_autochess_buffs import sync_chess_buffs
from resident_autochess_catalog import chess_activity, ordinary_chess_pool, prop_pool
from resident_autochess_props import purchase_prop
from resident_autochess_scene import initialize_scene

AUTOCHESS_COMMANDS = {89102, 89104, 89108, 89116, 89118, 89120, 89122, 89126,
    89128, 89130, 89134, 89138, 89140, 89142, 89144, 89146, 89148}


def prepare_max_body(game: dict) -> int:
    """推进进阶徽章的战前身体值峰值，只统计永久属性并剔除尚未确认的临时加成。

    ``launch_round`` 会用 ``manager_prepare_effects`` 把准备阶段加成叠加进 ``chess_list``，
    这些加成只在本轮战斗内有效，不属于玩家真实持有的属性。因此只在真实准备阶段
    （``state==1``，即无待结算战报）推进该峰值：待结算、已结算终局等其它状态下
    ``chess_list`` 含未确认加成，若此时重算，重复读取战报就会虚增 ``prepare_max_body``
    并提前解锁 1001 徽章。
    """
    if game.get('state') != 1:
        return game.get('prepare_max_body', 0)
    temporary = game.get('prepare_temporary', {})
    peak = 0
    for row in game['chess_list']:
        amount = temporary.get(str(row['unique_id']), [0, 0])
        peak = max(peak, chess_stat(row, 1) + chess_stat(row, 2) - sum(amount))
    return max(game.get('prepare_max_body', 0), peak)


def auto_state(user: dict) -> dict:
    """建立每种游戏模式的账号状态，断线后继续上次准备阶段。"""
    state = user.setdefault('resident', {}).setdefault('autochess', {})
    state.setdefault('games', {})
    state.setdefault('records', [])
    for game in state['games'].values():
        game.setdefault('activity_id', chess_activity(game.get('stage_id', 101), game.get('game_type', 0)))
        game['cleared_stages'] = [r['id'] for r in state.get('stage_list', [])]
        game['owned_cards'] = deepcopy(user.get('resident', {}).get('autochess_cards', {}).get('cards', {}))
        game['attrs'] = {int(k): v for k, v in game.get('attrs', {}).items()}
        # 旧版本曾把 stageID 写成 5，且没有初始角色，客户端会在准备页读取空对象。
        # 登录时修复一次，保证已有账号也能继续使用，不需要删除存档。
        stage_id = next((row['id'] for row in resident_rows('AutoChessStageCfg')
                         if row['id'] == game.get('stage_id')), 101)
        game['stage_id'] = stage_id
        game['attrs'][5] = stage_id
        game['attrs'].setdefault(1, 10)
        game['attrs'].setdefault(2, 0)
        game['attrs'].setdefault(3, 0)
        game['attrs'].setdefault(4, 0)
        game['attrs'].setdefault(6, 0)
        game['attrs'].setdefault(7, 0)
        game['attrs'].setdefault(8, 0)
        game['attrs'].setdefault(9, 1)
        game['attrs'].setdefault(10, 2)
        game['attrs'].setdefault(12, 1)
        game['attrs'].setdefault(16, 0)
        game['attrs'].setdefault(17, 0)
        game['attrs'].setdefault(18, 0)
        game['attrs'].setdefault(11, 1)
        game.setdefault('chess_list', [])
        initialize_scene(game)
        game.setdefault('next_uid', 200000)
        for index, chess in enumerate(game['chess_list'], 1):
            if chess.get('index', 0) < 1:
                chess['index'] = index
            attrs = chess.setdefault('chess_attr_list', [])
            keys = {row.get('key') for row in attrs}
            if 1 not in keys:
                attrs.append({'key': 1, 'value': 10})
            if 2 not in keys:
                attrs.append({'key': 2, 'value': 100})
            if 3 not in keys:
                attrs.append({'key': 3, 'value': 0})
            sync_chess_buffs(chess,game['exp_discount'])
        for index, item in enumerate(game.get('shop_items', []), 1):
            if item.get('index', 0) < 1:
                item['index'] = index
            if item['shop_type'] != 1 and any(key not in [r['key'] for r in item['chess_attr_list']] for key in (2, 3)):
                item['chess_attr_list'] = shop_item(item['shop_unique_id'], item['id'], item['index'], item['shop_type'])['chess_attr_list']
        player_id = next(row['id'] for row in resident_rows('AutoChessCfg') if row['type'] == 1 and game['activity_id'] in row['activity_id'])
        has_player = any(any(row['id'] == chess.get('chess_id') and row.get('type') == 1
                             for row in resident_rows('AutoChessCfg'))
                         for chess in game['chess_list'])
        if not has_player:
            game['chess_list'] = [chess_row(game['next_uid'], player_id, 1)] + game['chess_list']
            game['next_uid'] += 1
        repair_round_state(game)
        initialize_scene(game)
        if game['game_type']==1 and not game.get('classic_rules_version'):
            if game['state']!=0:
                game['player']['hp']=max(game['player']['hp'],max(0,chess_setting('auto_chess_lose_limit')-game['losses']))
                pending=game.get('pending_round')
                if pending:
                    game['chess_list']=deepcopy(pending.get('prepare_chess',game['chess_list']))
                    game.pop('pending_round');game.pop('last_settle_pushes',None);game['state']=1
                game['player']['battle_uid']=game['next_battle_uid']
            game['classic_rules_version']=1
        if game['game_type']==1:
            game['prepare_max_body']=prepare_max_body(game)
        if game['game_type'] == 0 and game['stage_id'] == chess_setting('auto_chess_boss_stage') and game['player']['wins'] >= 4:
            game['player']['boss'] = 1
            pending = game.get('pending_round')
            if pending:
                enemy = pending.get('replay',{}).get('auto_chessboard_info2',{}).get('chess_list',[])
                if len(enemy) != 1 or enemy[0]['chess_id'] != 17:
                    game['chess_list'] = deepcopy(pending.get('prepare_chess',game['chess_list']))
                    game.pop('pending_round')
                    game.pop('last_settle_pushes',None)
                    game['state'] = 1
            if any(b['buff_id'] == 541 for row in game['chess_list'] for b in row['buff_list']):
                game['boss_upgrade_chosen'] = True
            if game['state'] == 1 and not game.get('boss_upgrade_chosen'):
                from resident_autochess_upgrade import offer_manager_upgrade
                offer_manager_upgrade(game)
    return state


def base_info(game: dict) -> list[dict]:
    """只返回玩家生命、胜利和回合，商店属性通过attr_list发送。"""
    return player_base(game)


def chess_row(uid: int, chess_id: int, index: int = 0) -> dict:
    """生成 AutoChessData 可读取的棋子结构。"""
    cfg = resident_config('AutoChessCfg', chess_id)
    row = {'unique_id': uid, 'chess_id': chess_id, 'index': max(1, index),
        'chess_attr_list': [{'key': 1, 'value': cfg['body'][0]}, {'key': 2, 'value': cfg['body'][1]},
                            {'key': 3, 'value': 1}], 'buff_list': []}
    sync_chess_buffs(row)
    return row


def shop_item(uid: int, chess_id: int, index: int, shop_type: int = 0, game: dict | None = None) -> dict:
    """按客户端商店条目结构生成商品。"""
    cfg = resident_config('AutoChessItemCfg' if shop_type == 1 else 'AutoChessCfg', chess_id)
    row = {'shop_type': shop_type, 'shop_unique_id': uid, 'id': chess_id, 'index': index,
        'chess_attr_list': ([{'key': 1, 'value': cfg['shop_price']}] if shop_type == 1 else [
            {'key': 1, 'value': cfg['body'][0]}, {'key': 2, 'value': cfg['body'][1]}, {'key': 3, 'value': game['attrs'][12] if game else 1}]), 'is_lock': 0}
    if game is not None and shop_type!=1:
        from resident_autochess_global import apply_catalog_body
        apply_catalog_body(game,{'chess_attr_list':row['chess_attr_list']},chess_id)
    return row


def refill_shop(game: dict) -> None:
    """保留已锁定的商品编号与槽位，其余槽位按本地确定顺序补齐真实棋子。"""
    rows = ordinary_chess_pool(game)
    locked = [r for r in game['shop_items'] if r['is_lock']]
    rewards = [r for r in game['shop_items'] if r['shop_type'] == 2]
    game['shop_items'] = [next((r for r in locked if r['shop_type'] == 0 and r['index'] == i+1),
        shop_item(game['next_uid']+i, rows[(game['next_uid']+i) % len(rows)]['id'], i+1,0,game)) for i in range(5)]
    game['next_uid'] += 5
    bonus = game.get('shop_bonus', [0, 0])
    for item in game['shop_items']:
        if item not in locked:
            holder = {'chess_attr_list': item['chess_attr_list']}
            from resident_autochess_props import add_body
            add_body(holder, bonus[0], bonus[1])
    props = prop_pool(game)
    if props:
        game['shop_items'].append(next((r for r in locked if r['shop_type'] == 1 and r['index'] == 1),
            shop_item(game['next_uid'], props[game['next_uid'] % len(props)]['id'], 1, 1)))
        game['next_uid'] += 1
    game['shop_items'].extend(rewards)


def prepare_data(game: dict) -> dict:
    """返回 89101 的完整准备数据，保证商店、棋盘和升级字段始终存在。"""
    if game['game_type']==1:
        game['prepare_max_body']=prepare_max_body(game)
    return {'game_type': game['game_type'],
        'auto_chessboard_info': {'base_info_list': base_info(game),
            'chess_list': deepcopy(game['chess_list'])},
        'shop_items': deepcopy(game['shop_items']),
        'hero_upgrade_info': deepcopy(game.get('manager_upgrade', {'old_level': 0, 'new_level': 0, 'type': 0,
            'hero_upgrade_attrs': [], 'hero_upgrade_buffs': []})),
        'buff_list': deepcopy(game.get('global_buffs', [])), 'attr_list': shop_attributes(game), 'pve_restart_times': game['restart_times']}


def make_game(user: dict, game_type: int, stage_id: int = 0) -> dict:
    """创建确定性 PVE 对局，棋子与商店使用真实客户端配置。"""
    stage_id = resident_config('AutoChessStageCfg', stage_id or 101)['id']
    activity = chess_activity(stage_id, game_type)
    rows = resident_rows('AutoChessCfg')
    chess = next(r['id'] for r in rows if r['type'] == 0 and r['star'] == 1 and r['unlock_stage'] == 0 and activity in r['activity_id'])
    player_id = next(r['id'] for r in rows if r['type'] == 1 and activity in r['activity_id'])
    game = {'game_type': game_type, 'stage_id': stage_id, 'uuid': f'{user["uid"]}-{int(time.time()*1000)}',
        'state': 1, 'attrs': {1: 10, 2: 0, 3: 0, 4: 0, 5: stage_id, 6: 0, 7: 0, 8: 0, 9: 1, 10: 2, 11: 1, 12: 1, 16: 0, 17: 0, 18: 0},
        'chess_list': [chess_row(200000, player_id, 1)],
        'shop_items': [shop_item(100001+i, chess, i + 1) for i in range(5)],
        'next_uid': 200001, 'round': 1, 'replay': None, 'records': [], 'activity_id': activity,
        'cleared_stages': [r['id'] for r in user.get('resident', {}).get('autochess', {}).get('stage_list', [])],
        'owned_cards': deepcopy(user.get('resident', {}).get('autochess_cards', {}).get('cards', {}))}
    repair_round_state(game)
    initialize_scene(game)
    if game_type==1:game['classic_rules_version']=1
    props = prop_pool(game)
    if props:
        game['shop_items'].append(shop_item(100006, props[0]['id'], 1, 1))
    return game


def auto_pushes(user: dict, game_type: int) -> list[tuple]:
    """登录或操作后恢复自走棋页面、章节和空历史记录。"""
    state = auto_state(user)
    game = state['games'].get(str(game_type))
    result = []
    if game:
        # 先初始化准备数据，再更新状态；状态通知可能立即触发页面跳转。
        # 89101 已包含完整棋盘；登录时不要重复发送 89133，避免客户端二次重建棋子数据。
        result.extend([(89101, prepare_data(game)),
            (89125, {'game_type': game_type, 'state': game['state']})])
        if game.get('pending_round') and game['state'] in (3, 4, 5):
            result.append((89109, {'result': 0, 'replay_info': deepcopy(game['pending_round']['replay'])}))
            result.extend(round_messages(game))
    result.append((89201, stage_data(state)))
    return result


def stage_data(state: dict) -> dict:
    """仅下发实际通关记录，并按配置要求的全部馆主通关事实恢复基础徽章。"""
    medals = state.setdefault('medal',[])
    completed = {r['id']:r.get('time',0) for r in state.get('stage_list',[])}
    for cfg in resident_rows('AutoChessMedalCfg'):
        if not all(stage in completed for stage in cfg['unlock_stage']):
            continue
        row = next((r for r in medals if r['id'] == cfg['id']),None)
        if row is None:
            row = {'id':cfg['id'],'level':0,'unlock_time':0,'upgrade_time':0}
            medals.append(row)
        if row['level'] < 1:
            row.update(level=1,unlock_time=max(completed[stage] for stage in cfg['unlock_stage']))
    return {'stage_list': deepcopy(state.get('stage_list', [])), 'medal': deepcopy(state.get('medal', []))}


def autochess_pushes(user: dict, game_type: int) -> list[tuple]:
    """公开登录专用推送别名，避免初始化函数和请求路由循环导入。"""
    return auto_pushes(user, game_type)


def auto_request(user: dict, command: int, request: dict) -> tuple[dict, list]:
    """实现自走棋全部客户端操作，校验资源与重复请求并保存每一步。"""
    state = auto_state(user)
    game_type = request.get('game_type', 0)
    if command == 89116:
        if game_type not in (0, 1, 2, 3):
            raise ValueError('决斗王游戏模式无效')
        game = state['games'].get(str(game_type))
        if game and game['state'] not in (4, 5, 0):
            return {'result': 0}, auto_pushes(user, game_type)
        stage = resident_config('AutoChessStageCfg', request.get('other_param', 0) or 101)
        if game_type==1 and not any(r['id']==chess_setting('auto_chess_PVP_unlock') for r in state.get('stage_list',[])):
            raise ValueError('请先完成梵天道馆再进入决斗者之战')
        if game and game_type == 0 and game['state'] == 5 and stage['id'] == game['stage_id'] and game.get('pending_round'):
            retry_failed_round(game)
            return {'result': 0}, auto_pushes(user, game_type)
        if game and game['state'] == 4 and game.get('pending_round'):
            confirm_round(user, state, game)
        if game_type == 0 and stage['prev_stage'] and not any(r['id'] == stage['prev_stage'] for r in state.get('stage_list', [])):
            raise ValueError('请先完成前一决斗王关卡')
        game = make_game(user, game_type, stage['id'])
        state['games'][str(game_type)] = game
        return {'result': 0}, [(89201, stage_data(state)), (89101, prepare_data(game)),
            (89125, {'game_type': game_type, 'state': 1})]
    game = state['games'].get(str(game_type))
    if not game:
        raise ValueError('请先开始决斗王对局')
    if command in (89102, 89104, 89118, 89120, 89122, 89130, 89134, 89140) and game['state'] != 1:
        raise ValueError('决斗王当前不是准备阶段')
    if command == 89102:
        item = next((r for r in game['shop_items'] if r['shop_unique_id'] == request['shop_unique_id']), None)
        if item is None or item['shop_type'] != request['shop_type']:
            raise ValueError('商店棋子不存在或类型不符')
        if item['shop_type'] == 1:
            purchase_prop(game, item, request['to_index'])
        else:
            purchase_chess(game, item, request['to_index'])
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89104:
        rows = request['chess_list']
        indexes = [r['value'] for r in rows]
        if len(indexes) != len(set(indexes)) or any(i < 1 or i > game['attrs'][4] for i in indexes):
            raise ValueError('决斗王编队位置无效')
        existing = {r['key']: r for r in rows}
        if len(existing) != len(rows) or any(uid not in [r['unique_id'] for r in game['chess_list']] for uid in existing):
            raise ValueError('决斗王编队棋子不存在或重复')
        final_indexes = [existing[r['unique_id']]['value'] if r['unique_id'] in existing else r['index'] for r in game['chess_list']]
        if len(set(final_indexes)) != len(final_indexes):
            raise ValueError('决斗王编队槽位重叠')
        for chess in game['chess_list']:
            if chess['unique_id'] in existing:
                chess['index'] = existing[chess['unique_id']]['value']
        return {'result': 0, 'chess_list': deepcopy(rows)}, [(89101, prepare_data(game))]
    if command == 89118:
        free = game['attrs'].get(16,0)>0
        cost = 0 if free else game['attrs'][10]
        if game['attrs'][1] < cost:
            raise ValueError('决斗王刷新金币不足')
        game['attrs'][1] -= cost
        if free:game['attrs'][16] -= 1
        refill_shop(game)
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89120:
        if request['type'] not in (0, 1):
            raise ValueError('商店锁定状态无效')
        for info in request['info_list']:
            if info['shop_type'] not in (0, 1, 2): raise ValueError('商店类型无效')
            for uid in info['uid_list']:
                item = next((r for r in game['shop_items'] if r['shop_unique_id'] == uid), None)
                if item is None or item['shop_type'] != info['shop_type']: raise ValueError('锁定的商品不存在')
                item['is_lock'] = request['type']
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89122:
        item = next((r for r in game['chess_list'] if r['unique_id'] == request['uid']), None)
        if item is None: raise ValueError('出售的棋子不存在')
        cfg = next((r for r in resident_rows('AutoChessCfg') if r['id'] == item['chess_id']), None)
        if cfg and cfg.get('type') == 1:
            raise ValueError('不能出售玩家角色')
        level = min(len(cfg['sell_price']), 1 + sum(chess_stat(item, 3) >= n for n in cfg['exp']))
        owners=deepcopy(game['chess_list'])
        game['chess_list'].remove(item); game['attrs'][1] += cfg['sell_price'][level-1]
        from resident_autochess_transactions import prepare_event
        prepare_event(game,5,item,owners)
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89144:
        if game['state'] not in (1, 2):
            raise ValueError('决斗王当前不能结束准备')
        launch_round(user, game)
        game['state'] = 2
        return {'result': 0}, [(89125, {'game_type': game_type, 'state': 2})]
    if command == 89146:
        return {'result': 0, 'export_info': {'chess_list': [{'chess_id': r['chess_id'], 'index': r['index'],
            'chess_kv': deepcopy(r['chess_attr_list']), 'buffs': [{'buff_id': b['buff_id'],
                'chess_kv': deepcopy(b.get('other_param', []))} for b in r['buff_list']]}
            for r in game['chess_list']], 'user_buff': []}}, []
    if command == 89148:
        return {'result': 0}, []
    if command == 89108:
        if game['state'] not in (1, 2, 3, 4, 5):
            raise ValueError('决斗王当前没有可读取战报')
        launch_round(user, game)
        game['state'] = game['pending_round']['state']
        return {'result': 0, 'replay_info': deepcopy(game['pending_round']['replay'])}, [
            (89125, {'game_type': game_type, 'state': game['state']}), *round_messages(game)]
    if command == 89142:
        if not game.get('last_battle'):
            raise ValueError('决斗王尚无历史回放')
        return {'result': 0, 'replay_info': deepcopy(game['last_battle']['replay'])}, round_messages(game, True)
    if command == 89126:
        return {'result': 0}, confirm_round(user, state, game)
    if command == 89128:
        game['state'] = 0; game['chess_list'] = []; game['replay'] = None
        game.pop('pending_round', None)
        return {'result': 0}, [(89125, {'game_type': game_type, 'state': 0})]
    if command == 89130:
        source = next((r for r in game['chess_list'] if r['unique_id'] == request['source_uid']), None)
        loss = next((r for r in game['chess_list'] if r['unique_id'] == request['loss_uid']), None)
        if source is None or loss is None or source is loss or source['chess_id'] != loss['chess_id']:
            raise ValueError('合并棋子不存在或编号不匹配')
        merge_chess(source, loss,game.get('exp_discount',0))
        game['chess_list'].remove(loss)
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89140:
        buy_all_chess(game)
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89134:
        choose_manager_upgrade(game, request)
        return {'result': 0}, [(89101, prepare_data(game))]
    if command == 89138:
        return {'result': 0, 'enemy_auto_chess_board': battle_enemy(game)}, []
    raise ValueError('未知决斗王请求')
