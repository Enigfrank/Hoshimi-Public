"""谧光叙录的真实队伍、生命继承、重试快照与明确本地评分。"""

from copy import deepcopy
from functools import lru_cache

from gameplay_protocol import read_client_data
from resident_catalog import append_unique, resident_config
from resident_combat import combat_records
from resident_data import resident_state
from resident_hodur import HODUR_ACTIVITY, hodur_row, hodur_serial, validate_hodur_chapter


@lru_cache(maxsize=1)
def hodur_trial_rules() -> dict:
    """使用HodurConst中三对试用模板及力量锁定模块的实际定义。"""
    return read_client_data("(function()local c=require('HodurConst');local r={special=c.SPECIAL_AFFIX_ID,normal={},locked={}}"
        "for a,b in pairs(c.TRAIL_DIC)do r.normal[#r.normal+1]=a;r.locked[#r.locked+1]=b end return r end)()")


@lru_cache(maxsize=64)
def hodur_trial_hero(template: int) -> int:
    """原生生命统计用角色或皮肤编号，协议存档用玩家角色或试用模板编号。"""
    return read_client_data(f"(require('HeroStandardSystemCfg')[{int(template)}] or {{}}).hero_id or 0")


@lru_cache(maxsize=256)
def hodur_skin_hero(skin: int) -> int:
    """按本机皮肤配置还原角色编号，兼容原生报表的战斗皮肤。"""
    return read_client_data(f"(require('SkinCfg')[{int(skin)}] or {{}}).hero or {int(skin)}")


def prepare_hodur_battle(user: dict, request: dict, stage: int) -> dict:
    """按当前路线验证前置、事件、模块与存活队伍，并保存本局可结算的代数。"""
    common = request['common_info']
    if common.get('activity_id', 0) not in (0, HODUR_ACTIVITY) or common.get('battle_times', 1) != 1:
        raise ValueError('谧光叙录活动编号或倍率无效')
    stage_cfg = resident_config('ActivityHodurCfg', stage, 'stage_id')
    chapter = stage_cfg['chapter_id']
    cfg = validate_hodur_chapter(user, chapter)
    row = hodur_row(user, chapter)
    challenge = cfg['chapter_type'] == 2
    selected = [h for h in common['hero_list'] if h['hero_id']]
    ids = [h['hero_id'] for h in selected]
    actual = [hodur_trial_hero(h['hero_id']) if h['hero_type'] == 2 else h['hero_id'] for h in selected]
    if not 1 <= len(selected) <= 3 or 0 in actual or len(set(actual)) != len(actual):
        raise ValueError('谧光叙录队伍为空、重复或试用模板无效')
    opts = common.get('opts', [])
    if len(opts) > 1 or any(o['key'] != 1 for o in opts):
        raise ValueError('谧光叙录模块参数无效')
    affixes = list(opts[0]['value']) if opts else []
    if len(affixes) != len(set(affixes)):
        raise ValueError('谧光叙录模块重复')
    restarting = bool(request.get('is_restarted_battle', 0))
    if challenge:
        if any(i not in cfg['affix'] for i in affixes):
            raise ValueError('挑战模块不属于谧光叙录')
        rules = hodur_trial_rules()
        allowed = rules['locked'] if rules['special'] in affixes else rules['normal']
        if any(h['hero_type'] == 2 and h['hero_id'] not in allowed for h in selected):
            raise ValueError('谧光挑战试用模板与力量锁定规则不匹配')
        completed = [b['boss_id'] for b in row['challenge_boss_list'] if b['score'] > 0]
        repeat = restarting and completed and stage == completed[-1]
        if not repeat and (len(completed) == len(cfg['stage_list']) or stage != cfg['stage_list'][len(completed)][0]):
            raise ValueError('请按顺序挑战谧光关卡；仅可重试最新完成关卡')
        health = row['last_status'] if repeat else row['challenge_hero_stage_info']
        if row['challenge_hero_stage_info']:
            if set(affixes) != set(row['challenge_buff_info']):
                raise ValueError('挑战途中不能更换自限模块')
            if set(ids) != {h['hero_id'] for h in health if h['hero_blood'] > 0}:
                raise ValueError('挑战途中只能调整存活角色站位')
        else:
            row['challenge_buff_info'] = deepcopy(affixes)
            row['challenge_hero_stage_info'] = [{'hero_id': i, 'hero_blood': 1000000, 'hero_max_blood': 1000000} for i in ids]
            health = row['challenge_hero_stage_info']
        starting = deepcopy(health)
    else:
        fixed = resident_config('BattleHodurStageCfg', stage)['hero_list'][0][1]
        if len(selected) != 1 or selected[0]['hero_type'] != 2 or selected[0]['hero_id'] != fixed:
            raise ValueError('普通谧光章节必须使用配置的单人试用队伍')
        completed = [b['boss_id'] for b in row['normal_boss_id_list'] if b['result'] == 1]
        if stage_cfg['previous_stage'] and stage_cfg['previous_stage'] not in completed:
            raise ValueError('请先完成谧光前置战斗')
        if stage_cfg['event_lock']:
            event = resident_config('ActivityHodurEventCfg', stage_cfg['event_lock'], 'event_id')
            if not any(o in event['event_option'] for o in row['normal_option_info']):
                raise ValueError('请先选择谧光前置事件')
        if stage in completed and (not restarting or stage != completed[-1]):
            raise ValueError('已完成关卡只能从最新结算界面重试')
        expected = [int(resident_config('ActivityHodurEventOptionCfg', i, 'option_id')['buff_id']) for i in row['normal_option_info']]
        if set(affixes) != set(expected):
            raise ValueError('普通谧光模块与已选事件不一致')
        health = row['normal_hero_stage_info']
        if health and any(h['hero_blood'] == 0 for h in health):
            raise ValueError('谧光角色已倒下，请重置章节后重试')
        if not health:
            row['normal_hero_stage_info'] = [{'hero_id': fixed, 'hero_blood': 1000000, 'hero_max_blood': 1000000}]
        starting = deepcopy(row['normal_hero_stage_info'])
        repeat = False
    return {'chapter': chapter, 'serial': hodur_serial(user, chapter), 'team': deepcopy(selected),
            'affixes': affixes, 'start_health': starting, 'repeat': bool(repeat)}


def hodur_battle_health(team: list, info: dict) -> list:
    """配对角色或皮肤编号，保留合法三槽位空记录，缺失有效生命统计拒绝结算。"""
    result = []
    used = set()
    for raw in info.get('characters', []):
        identity, current, maximum = raw.get('1', 0), raw.get('2', 0), raw.get('3', 0)
        if not identity and not current and not maximum:
            continue
        if not identity or not 0 <= current <= maximum <= 4294967295 or not maximum:
            raise ValueError('谧光战报生命统计无效')
        actual = hodur_skin_hero(identity)
        matched = next((h for h in team if identity == h['hero_id'] or actual == (
            hodur_trial_hero(h['hero_id']) if h['hero_type'] == 2 else h['hero_id'])), None)
        if matched is None or matched['hero_id'] in used:
            raise ValueError('谧光战报角色与本局队伍不匹配')
        used.add(matched['hero_id'])
        result.append({'hero_id': matched['hero_id'], 'hero_blood': current, 'hero_max_blood': maximum})
    if used != {h['hero_id'] for h in team}:
        raise ValueError('谧光战报缺少角色生命统计')
    return sorted(result, key=lambda h: next(i for i, selected in enumerate(team) if selected['hero_id'] == h['hero_id']))


def hodur_score(info: dict, health: list, affixes: list) -> tuple[int, list]:
    """明确本地四项评分；连携／技能取原生12／7，加成取客户端模块point。"""
    records = combat_records(info)
    time = max(1, 100000 - max(0, info.get('2', 0)) // 1000 * 1000)
    blood = sum(h['hero_blood'] * 50000 // h['hero_max_blood'] for h in health) // len(health)
    combo = min(25000, records.get(12, 0) * 5000)
    skill = min(25000, records.get(7, 0) * 1000)
    detail = [time, blood, combo, skill]
    factor = 100
    for affix in affixes:
        public = resident_config('PublicBuffCfg', affix, 'ID')
        factor += resident_config('ActivityAffixPoolCfg', public['buff_class_id'])['point']
    return sum(detail) * factor // 100, detail


def complete_hodur_battle(user: dict, battle: dict, info: dict) -> None:
    """胜利保存实际血量、当轮成绩和历史；重试替换本关成绩，重置后的旧战斗拒绝。"""
    meta = battle['hodur']
    if meta['serial'] != hodur_serial(user, meta['chapter']):
        raise ValueError('谧光路线已重置，本场战斗不能结算')
    health = hodur_battle_health(meta['team'], info)
    state = resident_state(user)
    row = hodur_row(user, meta['chapter'])
    stage = battle['config']['id']
    cfg = validate_hodur_chapter(user, meta['chapter'])
    if cfg['chapter_type'] == 1:
        row['normal_hero_stage_info'] = health
        boss = next((r for r in row['normal_boss_id_list'] if r['boss_id'] == stage), None)
        if boss is None:
            row['normal_boss_id_list'].append({'boss_id': stage, 'result': 1})
        else:
            boss['result'] = 1
    else:
        previous = {h['hero_id']: deepcopy(h) for h in meta['start_health']}
        for h in health:
            previous[h['hero_id']] = h
        row['last_status'] = deepcopy(meta['start_health'])
        row['challenge_hero_stage_info'] = list(previous.values())
        score, detail = hodur_score(info, row['challenge_hero_stage_info'], meta['affixes'])
        row['score_list'] = detail
        boss = next(r for r in row['challenge_boss_list'] if r['boss_id'] == stage)
        boss['score'] = score
        boss['max_score'] = max(boss['max_score'], score)
        row['challenge_max_score'] = max(row['challenge_max_score'], sum(b['score'] for b in row['challenge_boss_list']))
        from resident_hodur_rank import save_hodur_best
        save_hodur_best(user, row, meta['affixes'])
        if all(b['score'] > 0 for b in row['challenge_boss_list']):
            append_unique(state.setdefault('hodur_completed_affix_counts', []), len(meta['affixes']))
