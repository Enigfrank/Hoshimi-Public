"""常驻音游、弹珠、料理、机装与赛车的结果及奖励。"""

from battle_rewards import grant_rewards
from currency_data import build_currency_balances
from gameplay_protocol import read_client_data
from resident_catalog import append_unique, resident_config
from resident_data import resident_state

MINIGAME_COMMANDS = {24052, 24054, 12120, 60054, 61048, 61050, 84332, 84334,
                     89002, 89004, 89006, 89008, 89402, 89404, 89702, 89704, 89706, 89708}


def reward_pairs(user: dict, pairs: list) -> list[dict]:
    """使用配置中的奖励数量，不允许请求指定奖励内容。"""
    rewards = [{'id': i, 'num': n} for i, n in pairs]
    grant_rewards(user, rewards)
    return rewards


def require_activity(request: dict, activity: int) -> None:
    """防止跨活动请求污染另一期的记录。"""
    if request.get('activity_id', activity) != activity:
        raise ValueError('常驻活动编号不匹配')


def pub_level_complete(pub: dict, level: int) -> bool:
    """按玩法类型读取料理、弹珠或战斗关卡的实际完成记录。"""
    from resident_pub import pub_level_complete as complete
    return complete(pub, level)


def minigame_request(user: dict, command: int, request: dict) -> dict:
    """校验关卡、前置和领取状态后保存本地玩法结果。"""
    state = resident_state(user)
    response = {'result': 0}
    if command == 24052:
        require_activity(request, 321201)
        if not 1 <= request['key'] <= 10:
            raise ValueError('溯梦标记无效')
        append_unique(state['skuld']['client_opt'], request['key'])
    elif command == 24054:
        require_activity(request, 321201)
        from resident_skuld import validate_skuld_level, complete_skuld_level
        cfg = validate_skuld_level(user, request['id'])
        if cfg['type'][0] != 2:
            raise ValueError('拼图关卡未开放或类型不匹配')
        response['reward_list'] = complete_skuld_level(user, cfg)
        grant_rewards(user, response['reward_list'])
    elif command == 12120:
        resident_config('StageArchivesCollectMomoTalkCfg', request['momotalk'], 'momotalk_plot_id')
        from resident_combat import validate_train_momotalk
        validate_train_momotalk(user, request['momotalk'])
        append_unique(state['momotalk'], request['momotalk'])
    elif command == 60054:
        ids = request['point_reward_id_list']
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('领奖列表为空或重复')
        pairs = []
        balances = build_currency_balances(user)
        for item in ids:
            cfg = resident_config('ActivityPointRewardCfg', item)
            from resident_combat import combat_point_data
            score = (balances.get(cfg['need_item_id'], 0) if cfg['need_item_id'] else
                     combat_point_data(user, 323801)['point'] if cfg['activity_id'] == 323801 else
                     state['scores'].get(str(cfg['system_key']), 0))
            if item in state['point_rewards'] or score < cfg['need']:
                raise ValueError('积分不足或奖励已领取')
            pairs.extend(cfg['reward_item_list'])
        response['reward_list'] = reward_pairs(user, pairs)
        state['point_rewards'].extend(ids)
    elif command == 61048:
        from resident_music import complete_music
        return complete_music(user, request)
    elif command == 61050:
        ids = request['id_list']
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('音游奖励列表无效')
        pairs = []
        for item in ids:
            cfg = resident_config('ActivityMusicCfg', item)
            row = next((r for r in state['music']['challenge_info'] if r['id'] == item), {})
            grade = read_client_data(f"require('GameSetting').attach_music_grade.value[{int(cfg['target'])}] or 0")
            if item in state['music_rewards'] or not row.get('sign') or row.get('score', 0) * 100 < cfg['total_score'] * grade:
                raise ValueError('音游目标未完成或已经领取')
            pairs.extend(cfg['reward'])
        response['reward_list'] = reward_pairs(user, pairs)
        state['music_rewards'].extend(ids)
    elif command in (89402, 89404):
        from resident_pinball import pinball_request
        return pinball_request(user, command, request)
    elif command in (89002, 89004, 89006, 89008):
        from resident_pub import pub_request
        return pub_request(user, command, request)
    elif command in (89702, 89704, 89706, 89708):
        from resident_vehicle import vehicle_request
        return vehicle_request(user, command, request)
    elif command in (84332, 84334):
        from resident_race import race_request
        return race_request(user, command, request)
    else:
        raise ValueError('未知常驻小游戏请求')
    return response
