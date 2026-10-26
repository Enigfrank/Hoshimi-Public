"""决斗王道馆场景畸变和升级奖励棋子的恢复。"""

from resident_catalog import resident_config
from resident_autochess_buffs import buff_record
from resident_autochess_round import chess_stat, chess_set_stat


def initialize_scene(game: dict) -> None:
    """读取场景配置并保存客户端全局增益；不回写已购买棋子的旧经验。"""
    game.setdefault('scene_buffs', resident_config('AutoChessStageCfg', game['stage_id'])['scene_buff'] or [])
    game['exp_discount'] = sum(resident_config('AutoChessBuffCfg', i)['param'][0]
        for i in game['scene_buffs'] if resident_config('AutoChessBuffCfg', i)['action_type'] == 12)
    if 621 in game['scene_buffs']:
        game['attrs'][9] = 1
    if 622 in game['scene_buffs']:
        game['attrs'][12] = resident_config('AutoChessBuffCfg',622)['param'][2]
    if game.get('scene_initialized'):
        return
    owner = next((r for r in game['chess_list'] if resident_config('AutoChessCfg',r['chess_id'])['type'] == 1), None)
    if owner is None:
        return
    game.setdefault('global_buffs', [])
    game.setdefault('next_buff_uid', 1000000)
    for buff_id in game['scene_buffs']:
        if not any(b['buff_id'] == buff_id for b in game['global_buffs']):
            game['global_buffs'].append(buff_record(game['next_buff_uid'],buff_id,owner))
            game['next_buff_uid'] += 1
    for item in game['shop_items']:
        if item['shop_type'] != 1:
            holder = {'chess_attr_list':item['chess_attr_list']}
            chess_set_stat(holder,3,max(chess_stat(holder,3),game['attrs'][12]))
    game['scene_initialized'] = True


def scene_shop_level(game: dict) -> int:
    """畸变621每回合升级；普通场景按原始3／5／7回合说明推进。"""
    return min(4,game['round']) if 621 in game.get('scene_buffs',[]) else min(4,1+sum(game['round']>=n for n in (3,5,7)))


def fill_reward_shop(game: dict) -> None:
    """客户端最多展示两个奖励槽，未领取奖励排队保存，领取后自动补位。"""
    game.setdefault('reward_queue', [])
    game['shop_items'] = [r for r in game['shop_items'] if r['shop_type'] != 2]
    for index,item in enumerate(game['reward_queue'][:2],1):
        item['index'] = index
        game['shop_items'].append(item)


def grant_scene_reward(game: dict, previous_level: int) -> None:
    """只在601场景实际商店升级时生成一次奖励，抽选使用稳定的合法配置顺序。"""
    if 601 not in game.get('scene_buffs',[]) or game['attrs'][11] <= previous_level:
        return
    from resident_autochess_catalog import ordinary_chess_pool
    from resident_autochess import shop_item
    pool = ordinary_chess_pool(game)
    chosen = pool[game['next_uid'] % len(pool)]
    item = shop_item(game['next_uid'],chosen['id'],1,2,game)
    game.setdefault('reward_queue', []).append(item)
    game['next_uid'] += 1
    fill_reward_shop(game)
