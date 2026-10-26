"""决斗王活动、关卡门槛与商店商品池。"""

from resident_catalog import resident_rows, resident_config
from gameplay_protocol import read_client_data


def chess_activity(stage_id: int, game_type: int) -> int:
    """按本机新版教程和PVP编号选择真实活动配置，旧PVE保持旧商品范围。"""
    tutorial = read_client_data("require('GameSetting').auto_chess_2_tutorial_stage_id.value[1]")
    key = 'ACTIVITY_AUTO_CHESS_5_0' if stage_id == tutorial or game_type == 3 else 'ACTIVITY_AUTO_CHESS_MAIN'
    return read_client_data(f"require('ActivityConst').{key}")


def available_chess_row(game: dict, row: dict) -> bool:
    """商品门槛读取原始GetStageState语义，关卡已开放就满足unlock_stage。"""
    if game['activity_id'] not in row['activity_id']:
        return False
    main = read_client_data("require('ActivityConst').ACTIVITY_AUTO_CHESS_MAIN")
    if game['activity_id'] != main:
        if 'relevant_type' not in row and main not in row['activity_id']:
            return any(r['group_id'] == row['group_id'] and game.get('owned_cards', {}).get(str(r['id']), 0) > 0
                for r in resident_rows('AutoChessCardCfg'))
        return True
    stage = row.get('unlock_stage', 0)
    if not stage:
        return True
    cfg = resident_config('AutoChessStageCfg', stage)
    return not cfg['prev_stage'] or cfg['prev_stage'] in game.get('cleared_stages', [])


def ordinary_chess_pool(game: dict, star: int | None = None) -> list:
    """仅返回当前活动中合法开放的普通棋子，变身和召唤可指定精确星级。"""
    return [r for r in resident_rows('AutoChessCfg') if r['type'] == 0 and available_chess_row(game, r)
        and (r['star'] == star if star is not None else r['star'] <= game['attrs'][11])]


def prop_pool(game: dict) -> list:
    """按当前商店星级和真实关卡门槛生成道具池。"""
    return [r for r in resident_rows('AutoChessItemCfg') if r['star'] <= game['attrs'][11] and available_chess_row(game, r)]
