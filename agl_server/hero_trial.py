"""试用关卡的队伍校验与模板归一化。"""


def prepare_hero_trial_battle(common: dict, config: dict) -> None:
    """试用关卡只允许配置的单个试用角色进入。

    关卡配置是唯一依据：客户端无论提交角色编号还是试用模板编号、英雄类型
    按已拥有还是试用填写，都统一改写为模板编号，后续由 ``trial_battle_hero``
    按模板生成完整战斗属性，不借用玩家养成数据。
    """
    trial = next((row for row in config.get('hero_list') or [] if row and row[0]), None)
    if not trial or len(trial) < 2 or not trial[1]:
        raise ValueError('试用关卡未配置有效的试用角色')
    supplied = [hero for hero in common.get('hero_list', []) if hero['hero_id']]
    if len(supplied) != 1 or supplied[0]['hero_id'] not in (trial[0], trial[1]):
        raise ValueError('试用关卡必须使用配置的试用角色')
    entry = dict(supplied[0])
    entry['hero_id'] = trial[1]
    entry['hero_type'] = 2
    common['hero_list'] = [entry]
