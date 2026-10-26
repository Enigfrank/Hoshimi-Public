"""食与异世界的完整关卡前置、料理资格、伙伴与首次奖励。"""

from battle_rewards import drop_config, roll_drops, grant_rewards
from currency_data import build_currency_balances
from resident_catalog import resident_config, resident_rows, append_unique
from resident_data import resident_state


def pub_level_complete(pub: dict, level: int) -> bool:
    """按配置区分料理、普通弹珠和战斗；困难弹珠不代替普通前置。"""
    cfg = resident_config('SummerPubLevelCfg', level)
    if cfg['type'] == 0:
        return any(r['stage_id'] == cfg['stage_id'][0] and r['state'] == 2 for r in pub['cook_stage_list'])
    return cfg['stage_id'][0] in pub['stage_list'] if cfg['type'] == 2 else level in pub['battle_stage_list']


def require_pub_level(pub: dict, cfg: dict) -> None:
    """以客户端普通关卡完成事实校验前一关和显式前置。"""
    required = ([cfg['prev_id']] if cfg['prev_id'] else []) + list(cfg['pre_need_unlock_id_list'] or [])
    if any(not pub_level_complete(pub, level) for level in required):
        raise ValueError('料理前置关卡尚未完成')


def require_pub_cook(user: dict, cfg: dict) -> None:
    """食材为永久解锁资格；末道料理的自身条件按其前一关解释。"""
    pub = resident_state(user)['pub']
    for level in cfg['unlocked_by_level']:
        row = resident_config('SummerPubLevelCfg', level)
        if row['type'] == 0 and cfg['id'] in row['stage_id']:
            require_pub_level(pub, row)
        elif not pub_level_complete(pub, level):
            raise ValueError('料理前置关卡尚未完成')
    balances = build_currency_balances(user)
    if any(balances.get(item, user.get('materials', {}).get(str(item), 0)) <= 0 for item in cfg['material']):
        raise ValueError('料理食材尚未获得')


def pub_partners(pub: dict, partner_type: int = 0) -> list:
    """从配置中的招募关卡恢复伙伴，制作但未观看剧情不算招募完成。"""
    return [cfg['id'] for cfg in resident_rows('SummerPubHeroCfg')
        if cfg['activity_id'] == 3539101 and (not partner_type or cfg['type'] == partner_type)
        and cfg['recruit_level'] and pub_level_complete(pub, cfg['recruit_level'])]


def validate_pub_stage(user: dict, stage: int) -> None:
    """校验真实战斗／故事前置；末道料理的剧情占位关卡要求食材已解锁。"""
    pub = resident_state(user)['pub']
    cfg = next((c for c in resident_rows('SummerPubLevelCfg') if c['type'] == 1 and stage in c['stage_id']), None)
    if cfg:
        require_pub_level(pub, cfg)
    elif stage == 5270208:
        require_pub_cook(user, resident_config('SummerPubCookCfg', 10800))
    else:
        raise ValueError('料理战斗不属于当前常驻活动')


def complete_pub_battle(user: dict, stage: int) -> list:
    """首通逻辑关卡发放其独立食材掉落，实际战斗编号和玩法编号共享记录。"""
    pub = resident_state(user)['pub']
    rewards = []
    for cfg in resident_rows('SummerPubLevelCfg'):
        if cfg['type'] == 1 and stage in cfg['stage_id']:
            if append_unique(pub['battle_stage_list'], cfg['id']) and cfg['drop_lib_id']:
                rewards.extend(roll_drops(drop_config(cfg['drop_lib_id']), 1))
    if stage == 5270208:
        row = next((r for r in pub['cook_stage_list'] if r['stage_id'] == 10800), None)
        if row is None:
            pub['cook_stage_list'].append({'stage_id': 10800, 'state': 2})
        else:
            row['state'] = 2
    return rewards


def pub_request(user: dict, command: int, request: dict) -> dict:
    """执行弹珠、料理、阅读和昼夜切换；失败不授予图鉴或首通奖励。"""
    if request.get('activity_id', 3539101) != 3539101:
        raise ValueError('常驻活动编号不匹配')
    pub = resident_state(user)['pub']
    if command == 89008:
        pub['time_state'] = 2 if pub['time_state'] == 1 else 1
    elif command == 89004:
        row = next((r for r in pub['illustrated'] if r['illustrated_id'] == request['ill_id']), None)
        if row is None:
            raise ValueError('料理图鉴尚未获得')
        row['view_state'] = 2
    elif command == 89006:
        cfg = resident_config('SummerPubCookCfg', request['stage_id'])
        phase = request['stage']
        if phase not in (1, 2):
            raise ValueError('料理阶段无效')
        row = next((r for r in pub['cook_stage_list'] if r['stage_id'] == cfg['id']), None)
        if row and row['state'] >= phase:
            return {'result': 0}
        require_pub_cook(user, cfg)
        if phase == 2 and row is None:
            raise ValueError('请先完成料理制作')
        if row is None:
            row = {'stage_id': cfg['id'], 'state': 0}
            pub['cook_stage_list'].append(row)
        row['state'] = phase
    elif command == 89002:
        cfg = resident_config('SummerPubLevelCfg', request['stage_id'])
        if cfg['type'] != 2 or request['sub_stage_id'] not in cfg['stage_id'] or request['result'] not in (1, 2):
            raise ValueError('弹珠子关卡或结算结果无效')
        require_pub_level(pub, cfg)
        for item in request['illustrated']:
            if resident_config('BilliardIllustrationsCfg', item)['activity_id'] != 3539101:
                raise ValueError('图鉴不属于当前活动')
        if request['result'] == 1:
            if append_unique(pub['stage_list'], request['sub_stage_id']):
                grant_rewards(user, roll_drops(drop_config(cfg['drop_lib_id']), 1))
            for item in request['illustrated']:
                if not any(r['illustrated_id'] == item for r in pub['illustrated']):
                    pub['illustrated'].append({'illustrated_id': item, 'view_state': 1})
    else:
        raise ValueError('未知料理请求')
    return {'result': 0}
