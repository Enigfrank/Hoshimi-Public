"""试用关卡的队伍校验与模板归一化。"""


def prepare_hero_trial_battle(common: dict, config: dict) -> None:
    """校验关卡指定的全部试用成员，并按请求顺序使用配置模板。"""
    templates = {hero: template for hero, template in (config.get('hero_list') or []) if hero}
    if not templates or any(not template for template in templates.values()):
        raise ValueError('试用关卡未配置有效的试用角色')
    supplied = [hero for hero in common.get('hero_list', []) if hero['hero_id']]
    remaining = templates.copy()
    team = []
    for selection in supplied:
        selected_id = selection['hero_id']
        hero = next((hero for hero, template in remaining.items() if selected_id in (hero, template)), None)
        if hero is None:
            raise ValueError('试用关卡必须使用配置的试用角色')
        team.append({**selection, 'hero_id': remaining.pop(hero), 'hero_type': 2})
    if remaining:
        raise ValueError('试用关卡必须使用配置的试用角色')
    common['hero_list'] = team
