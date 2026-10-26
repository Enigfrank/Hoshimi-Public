"""旧版决斗者之战的本地对手、结算规则、计分与进阶徽章事实。"""

from copy import deepcopy
import time

from resident_catalog import resident_config, resident_rows
from resident_autochess_round import chess_setting, chess_stat


def match_distance(left: int, right: int) -> int:
    """整数差距使用双端都能精确执行的最大／最小值，不依赖额外运行函数。"""
    return max(left,right)-min(left,right)


def classic_enemy(game: dict) -> dict:
    """只使用本机type=2队伍，以回合／胜场／生命匹配并按ID稳定选择。"""
    rows=[r for r in resident_rows('AutoChessTeamCfg') if r['type']==2]
    if not rows:raise ValueError('本机缺少决斗者之战对手')
    return min(rows,key=lambda r:(match_distance(r['round'],game['round']),match_distance(r['win_num'],game['player']['wins']),
        match_distance(r['hp_num'],game['player']['hp']),r['id']))


def player_damage(rows: list) -> int:
    """312为持有期间的玩家伤害，独立于准备格子／玩家回合键，不重复安装。"""
    return 1+sum(resident_config('AutoChessBuffCfg',b['buff_id'])['param'][2]
        for row in rows for b in row['buff_list'] if b['buff_id'] in (3121,3122,3123))


def classic_score(game: dict) -> tuple:
    """明确本地基础计分，只计算可确定的胜场、生命、回合与无败，不猜特殊成就。"""
    wins=game['player']['wins'];hp=max(0,game['player']['hp'])
    win_limit=chess_setting('auto_chess_win_limit');hp_limit=chess_setting('auto_chess_lose_limit')
    points={10011:resident_config('AutoChessPVPScoreCfg',10011)['param1'] if wins>=win_limit and game['losses']==0 else 0,
        10021:resident_config('AutoChessPVPScoreCfg',10021)['param1']*wins//win_limit,
        10031:max(0,resident_config('AutoChessPVPScoreCfg',10031)['param1']-(hp_limit-hp)*resident_config('AutoChessPVPScoreCfg',10031)['param2']),
        10041:max(0,resident_config('AutoChessPVPScoreCfg',10041)['param2']-game['round'])*resident_config('AutoChessPVPScoreCfg',10041)['param1']}
    details=[{'key':key,'value':points[key]} for key in sorted(points)]
    return sum(points.values()),details


def confirm_medal_facts(state: dict, game: dict, facts: dict) -> list:
    """已确认单局统计推进进阶，召唤仅取单回合，失败也只计实际发生的效果。"""
    stats=game.setdefault('medal_stats',{'max_body':0,'max_summons':0,'shields':0,'hurt_effects':0})
    stats['max_body']=max(stats['max_body'],game.get('prepare_max_body',0),facts.get('max_body',0))
    stats['max_summons']=max(stats['max_summons'],facts.get('summons',0))
    stats['shields']+=facts.get('shields',0);stats['hurt_effects']+=facts.get('hurt_effects',0)
    targets={1001:stats['max_body']>=200,1002:stats['max_summons']>=6,
        1003:stats['shields']>=30,1004:stats['hurt_effects']>=120}
    unlocked=[]
    for row in state.get('medal',[]):
        if row['level']==1 and targets.get(row['id'],False):
            row.update(level=2,upgrade_time=int(time.time()));unlocked.append(row['id'])
    game.setdefault('earned_medals',[]).extend(unlocked)
    return unlocked


def classic_record(game: dict, result: int) -> dict:
    """保存原始p89 battle_record，荣誉记录与普通历史共享完整对局描述。"""
    from resident_autochess_round import player_base
    point,details=classic_score(game)
    return {'time':int(time.time()),'result':result,'auto_chessboard_info':{
        'base_info_list':player_base(game),'chess_list':deepcopy(game['chess_list'])},
        'point':point,'point_detail':details,'medal_ids':list(game.get('earned_medals',[]))}


def classic_history(state: dict) -> dict:
    """查询仅返回旧版PVP真实记录，不把PVE或新版排行结构写入p89协议。"""
    return {'result':0,'record':deepcopy(state.get('classic_records',[])),
        'medal_record':deepcopy(state.get('medal_records',[]))}
