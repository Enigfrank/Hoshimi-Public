"""多维变量的本地路线、奖励与保存点；路线独立于官服随机排期。"""

from copy import deepcopy
from functools import lru_cache
import random

from .battle_rewards import grant_rewards, spend_items
from .client_attributes import polyhedron_attributes
from .currency_data import build_currency_balances
from .gameplay_protocol import decode_message, encode_message, read_client_data

POLYHEDRON_COMMANDS = set(range(18010, 18035, 2)) | {66000, 66002, 66004, 66034, 66036, 66038}

# 2026-11-23 官服抓包实测：sc_18005 的经验按已完成战斗层数 N 计。
# 难度、信标、终端天赋都不改变这两个单价。
DECISION_EXP_PER_BATTLE = 16
TERMINAL_EXP_PER_BATTLE = 220

# 官服 18001 实测：attribute_list 的基础值来自 PolyhedronSettingCfg，
# 其余数值由 action=1 的终端/信标效果按 id 叠加。id8 是“珍宝奖励可重置次数”，
# 官服 1-3 初始为 5；本地必须下发，否则客户端 GetRollRewardCount() 读到 0，刷新按钮不显示。
ATTRIBUTE_BASE = {1: 3, 2: 500, 3: 3, 4: 30, 5: 3, 15: 5, 16: 3, 29: 1000, 30: 3}


@lru_cache(maxsize=1)
def polyhedron_catalog() -> dict:
    """读取本机可用路线层、关卡、奖励物品和天赋配置。"""
    return read_client_data('''(function()
        local r={}
        for _,entry in ipairs({{'tiers','PolyhedronTierCfg'},{'stages','BattlePolyhedronStageCfg'},
            {'events','PolyhedronEventCfg'},{'artifacts','PolyhedronArtifactCfg'},
            {'effects','PolyhedronEffectCfg'},{'beacons','PolyhedronBeaconCfg'},
            {'terminal','PolyhedronTerminalCfg'},{'terminal_levels','PolyhedronTerminalLevelCfg'},
            {'settings','PolyhedronSettingCfg'},{'heroes','PolyhedronHeroCfg'},
            {'difficulties','PolyhedronDifficultyCfg'},{'policy','PolyhedronPolicyCfg'},
            {'gates','PolyhedronGateCfg'}}) do
            local c=require(entry[2]);local rows={}
            for _,id in ipairs(c.all) do rows[#rows+1]=c[id] end;r[entry[1]]=rows
        end
        return r
    end)()''')


def config_row(kind: str, item: int) -> dict:
    """按真实配置 ID 查询多维条目，未知 ID 直接拒绝。"""
    row = next((r for r in polyhedron_catalog()[kind] if r['id'] == item), None)
    if row is None:
        raise ValueError('多维配置不存在')
    return row


def effect_value(data: dict, key: int) -> int:
    """汇总 action1 效果中指定参数键的数值；信标/终端的经济与门类效果共用。"""
    total = 0
    for row in data.get('game', {}).get('progress', {}).get('effect_list') or []:
        effect = config_row('effects', row['id'])
        if effect.get('action') != 1:
            continue
        for param in effect.get('params') or []:
            if len(param) >= 2 and int(param[0]) == int(key):
                total += int(param[1])
    return total


def refresh_attribute_list(data: dict) -> None:
    """把 effect_list 汇总成客户端读取的 attribute_list。

    id8 是“珍宝奖励可重置次数”：首次下发时取基础值+效果值；玩家用掉一次后
    由 18030 直接递减，normalize 必须保留当前剩余次数，不能每次重算回满。
    """
    progress = data.get('game', {}).get('progress')
    if not isinstance(progress, dict):
        return
    current = {int(r['id']): int(r.get('value') or 0)
               for r in progress.get('attribute_list') or []}
    # 只有完整下发过 34 项属性时才认为 id8 是“已扣减的剩余次数”；
    # 旧存档的 [] 或半截列表必须重新按效果计算，不能把 0 当成用完。
    keep_id8 = len(current) >= 34 and 8 in current
    rows = []
    for attr_id in range(1, 35):
        if attr_id == 8 and keep_id8:
            value = current[attr_id]
        else:
            value = int(ATTRIBUTE_BASE.get(attr_id, 0)) + effect_value(data, attr_id)
        rows.append({'id': attr_id, 'value': value})
    progress['attribute_list'] = rows


def _artifact_max_level(data: dict, artifact: dict) -> int:
    """终端 3008/3013 的“可升级珍宝等级上限+1”只作用于原本可升级的珍宝。"""
    base = int(artifact['max_level'])
    if base <= 1:
        return base
    return base + max(0, effect_value(data, 5))


def _upgrade_random_artifact(data: dict, count: int = 1) -> None:
    """按已拥有且未满级随机升 1 级；终端 3002、9003 共用。"""
    progress = data['game']['progress']
    for _ in range(max(0, int(count))):
        rows = [a for a in progress.get('artifact_list') or []
                if a['level'] < _artifact_max_level(data, config_row('artifacts', a['id']))]
        if not rows:
            return
        target = random.choice(rows)
        target['level'] += 1


def _terminal_gate_categories(data: dict) -> set[int]:
    """settings18 第二段列出必须由终端解锁的门类；本地当前是 [3006]。"""
    try:
        return {int(x) for x in (config_row('settings', 18)['value'][1] or [])}
    except (ValueError, IndexError, TypeError):
        return set()


NORMAL_GATE_TYPES = {1, 1002, 2, 3, 3002, 4, 5, 8}
ELITE_GATE_TYPES = {1002, 3002}
SPECIAL_GATE_TYPES = {10001, 10002}
BOSS_CHAPTER_LEVELS = {1: 7, 2: 8, 3: 10}
# 第一/二章默认 2 扇门，第三章默认 3 扇门；异常节点是额外独立门，不占普通门名额。
GATE_COUNT_BY_CHAPTER = {1: 2, 2: 2, 3: 3}
SPECIAL_GATE_CHANCE = 0.1
# 特殊门基础概率 10%：1-1 与各章 BOSS 节点除外，其余节点按概率生成。
# 带有相关增益时，按下方 effect_value 叠加，上限仍为 100%。
# 只有 1-1 保底不生成精英门；之后每扇普通门约 8% 概率替换为精英门，
# 约 12 扇门出 1 扇。同一节点最多 1 扇精英，避免整层全紫。
ELITE_GATE_CHANCE = 0.08
# 3035199 是官服 9002「战前整备」的非战斗奖励地图；1-1 只发专属珍宝，首战从 1-2 开始。
INITIAL_STAGE_ID = 3035199
# 官服 2026-11-23 抓包：第一章 BOSS 门 event6001 使用 3035303，
# 第二章 event6002 使用 3036303，第三章 event7001 使用 3037304。
# 3035601/3036601/3037601 是补给节点地图，不能拿来当 BOSS 战。
BOSS_STAGE_IDS = {1: 3035303, 2: 3036303}
FINAL_STAGE_ID = 3037304
RECRUIT_STAGE_IDS = {1: 3035401, 2: 3036401, 3: 3037401}
SHOP_STAGE_IDS = {1: 3035601, 2: 3036601, 3: 3037601}
# 额外挑战不是普通战斗图。官服 3-4 的 10001022 实测使用 3037523，
# 对应第一/二/三章的 3035523/3036523/3037523 特殊挑战场景（金圈、1 分钟刷怪）。
# 三类额外挑战地图：521 = 一波 7 只计时，522 = 10 波 × 6 只，523 = 保护 A-02。
# 官服抓包 10001022 → 3037523 对应 A-02；521/522 按同章地图后缀延展。
SPECIAL_STAGE_IDS = {
    1: {'time': 3035521, 'kill': 3035522, 'a02': 3035523},
    2: {'time': 3036521, 'kill': 3036522, 'a02': 3036523},
    3: {'time': 3037521, 'kill': 3037522, 'a02': 3037523},
}
# 10001 类额外挑战（原货币/珍宝池）统一改成 A-02 护送；10002 末位 1/2 是计时，
# 3/4 是六十怪击杀挑战。
SPECIAL_TIME_EVENT_TAILS = {1, 2, 11, 12, 21, 22}
# 保护 A-02 巡查机特殊关：奖励按 A-02 剩余生命比例结算。
SPECIAL_ROBOT_IDS = {2019, 6098}
SPECIAL_RETURN_KEY = 'polyhedron_special_return'
BOSS_MEDICAL_EFFECT = 203


def _tier_index(tier_id: int) -> int:
    tiers = polyhedron_catalog()['tiers']
    return next(i for i, t in enumerate(tiers) if t['id'] == tier_id)


def _next_tier(tier_id: int) -> dict | None:
    tiers = polyhedron_catalog()['tiers']
    index = _tier_index(tier_id)
    return tiers[index + 1] if index + 1 < len(tiers) else None


def _event_of_type(event_type: int, preferred_id: int = 0) -> dict:
    rows = [e for e in polyhedron_catalog()['events'] if e['event_type'] == event_type]
    if not rows:
        raise ValueError('多维事件类型没有配置')
    if preferred_id:
        row = next((e for e in rows if e['id'] == preferred_id), None)
        if row:
            return row
    # 特殊门（10001/10002）下有多条同类型事件：金币、珍宝、计时金币等。
    # 固定取 rows[0] 会让特殊门永远只出金币事件，珍宝类特殊事件永远不出现。
    if event_type in SPECIAL_GATE_TYPES:
        return random.choice(rows)
    return rows[0]


def _event_type_of_id(event_id: int) -> int:
    """10001001 -> 10001；10002003 -> 10002。"""
    text = str(int(event_id))
    return 10000 + int(text[3:5]) if len(text) >= 5 else 0


def _special_event_for_chapter(chapter: int) -> dict | None:
    """按 settings22 的章节权重抽特殊门；地图与奖励按三类挑战拆分。"""
    try:
        rows = config_row('settings', 22)['value']
    except (ValueError, TypeError):
        rows = []
    pairs = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        if int(row[0]) != int(chapter):
            continue
        pairs = row[1] or []
        break
    candidates = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        weight, event_id = int(pair[0]), int(pair[1])
        event_type = _event_type_of_id(event_id)
        if event_type not in SPECIAL_GATE_TYPES:
            continue
        row = next((e for e in polyhedron_catalog()['events']
                    if int(e['id']) == event_id), None)
        if row and weight > 0:
            candidates.append((weight, row))
    if not candidates:
        return None
    return random.choices([row for _, row in candidates],
                          weights=[weight for weight, _ in candidates], k=1)[0]


def _chapter_stages(chapter: int) -> list[dict]:
    prefix = ('3035', '3036', '3037')[chapter - 1]
    rows = [stage for stage in polyhedron_catalog()['stages']
            if str(stage['id']).startswith(prefix)]
    if not rows:
        raise ValueError(f'多维第 {chapter} 章没有可用关卡')
    return rows


def _special_mode(event_id: int) -> str:
    """10001 类走 A-02；10002 末位 1/2 是一波 7 只计时，3/4 是六十怪击杀。"""
    text = str(int(event_id))
    if text.startswith('10001'):
        return 'a02'
    tail = int(text[-2:])
    return 'time' if tail in SPECIAL_TIME_EVENT_TAILS else 'kill'


def _stage_for_tier(tier: dict, event_type: int, stage_offset: int = 0,
                    event_id: int = 0) -> int:
    """按章节、事件类型和节点序号取关卡；精英战必须进精英地图。"""
    chapter, level = int(tier['tier']), int(tier['level'])
    if event_type == 7:
        return FINAL_STAGE_ID
    if event_type == 6:
        return BOSS_STAGE_IDS.get(chapter, FINAL_STAGE_ID)
    if event_type == 2:
        return RECRUIT_STAGE_IDS.get(chapter, 3035401)
    if event_type in (5, 5001, 5002, 5003):
        return SHOP_STAGE_IDS.get(chapter, 3035601)
    if event_type in SPECIAL_GATE_TYPES:
        variants = SPECIAL_STAGE_IDS.get(chapter, SPECIAL_STAGE_IDS[3])
        return variants.get(_special_mode(event_id), variants['a02'])
    # 官服抓包唯一明确的第一章精英入口：1-5 通往 1-6 使用 3035208。
    if (chapter, level) == (1, 5) and event_type in ELITE_GATE_TYPES:
        return 3035208
    rows = _chapter_stages(chapter)
    # 官服抓包：普通战地图尾号 105..112，精英战地图尾号 205..208。
    # 旧代码把 5205..5208 精英图和 552x/554x 特殊图混进普通池，导致
    # 精英门点进去仍加载普通小怪关。
    tails = range(205, 209) if event_type in ELITE_GATE_TYPES else range(105, 113)
    candidates = [row for row in rows if int(str(row['id'])[-3:]) in tails]
    if not candidates:
        raise ValueError('多维章节没有可用战斗关卡')
    return candidates[(level - 1 + int(stage_offset)) % len(candidates)]['id']


def route_event(tier: dict, event_type: int, reward_type: int = 0, stage_offset: int = 0,
                event_id: int = 0) -> dict:
    """按节点类型生成真实事件及所属章节关卡，不再用活动层数硬套地图。"""
    event = _event_of_type(event_type, event_id)
    # 门的 reward_type 必须与 PolyhedronGateCfg 的 (event_type, reward_type) 对齐：
    # 客户端 GetGateCfg 用这两个字段找 gate_des，旧代码把货币门/升级门的
    # reward_type 统一清成 0，导致门标题退回“前往1-4”、说明文字为空。
    category = int(reward_type)
    # 9001/9002 是 1-1 的非战斗整备奖励，官服固定停在 3035199；不能套普通战斗地图。
    initial = int(event['id']) in (9001, 9002)
    stage_id = INITIAL_STAGE_ID if initial else _stage_for_tier(tier, event_type, stage_offset, event_id)
    result = {'id': event['id'], 'stage_id': stage_id, 'reward_type': category}
    if event_type == 2:
        # 官服招募事件 progress.event.reward_type 固定为 2（TEAMMATE）。
        # 客户端 PolyhedronInteractiveHeroView 只有在 reward_type==2 时点击角色才会
        # 发送 cs_18020；为 0 时所有交互都会走 DoPolyhedronNextTrigger，表现为“点不了/不能交互”。
        result['reward_type'] = 2
    return result


def _lone_wolf(data: dict) -> bool:
    """孤狼之道：本次多维路线不生成任何招募关卡。"""
    start = data.get('game', {}).get('start_info') or {}
    try:
        if 10 in {int(x) for x in start.get('beacon_id_list') or []}:
            return True
    except (TypeError, ValueError):
        pass
    for effect in data.get('game', {}).get('progress', {}).get('effect_list') or []:
        try:
            eid = int(effect.get('id') if isinstance(effect, dict) else effect)
        except (TypeError, ValueError):
            continue
        if eid == 111:
            return True
    return False


def _gate_events(tier: dict, gates: list[dict], no_recruit: bool = False,
                 data: dict | None = None) -> list[dict]:
    """按章节给 2/2/3 扇普通门；异常节点是额外独立紫色门，不占普通门名额。"""
    if tier['level'] == BOSS_CHAPTER_LEVELS[int(tier['tier'])]:
        chapter = int(tier['tier'])
        event_type = 7 if chapter == 3 else 6
        # 官服抓包：1-6 结算后 gate_list=[{index:3,event:6001}]，2-8 是 6002，
        # 终章 BOSS 是 7001。index=3 才会让单扇红门落在三扇门布局的正中。
        event_id = {1: 6001, 2: 6002, 3: 7001}[chapter]
        return [{'index': 3, 'event': route_event(tier, event_type, 0, 0, event_id)}]
    base_count = GATE_COUNT_BY_CHAPTER.get(int(tier['tier']), 3)
    # 精英门单独按概率替换，不和普通门同池等权抽样。
    choices = [g for g in gates if int(g['event_type']) in NORMAL_GATE_TYPES
               and int(g['event_type']) not in ELITE_GATE_TYPES]
    elite_choices = [g for g in gates
                     if int(g['event_type']) in ELITE_GATE_TYPES]
    # 只有 1-1 明确不出现精英门；1-2 起都可以低概率出现。
    allow_elite = not (int(tier['tier']) == 1 and int(tier['level']) == 1)
    if data is not None:
        # 官服抓包首次升级门是 3-1（event 8003），1、2 章不出现升级门；
        # 且必须至少有 3 件已拥有未满级珍宝，否则升级页只能出 1 张卡。
        if int(tier['tier']) < 3 or len(_upgrade_artifact_candidates(data)) < 3:
            choices = [g for g in choices if int(g['event_type']) != 8]
        # 万象寓于门（3006）默认不入池；终端 3103 的 [23,2] 解锁后才允许出现。
        if effect_value(data, 23) <= 0:
            locked = _terminal_gate_categories(data)
            choices = [g for g in choices if int(g.get('reward_type') or 0) not in locked]
        # 佣兵赌注：第一层额外增加一个关卡选项。
        if int(tier['tier']) == 1 and int(tier['level']) == 1:
            base_count += max(0, effect_value(data, 34))
    if no_recruit:
        choices = [g for g in choices if int(g['event_type']) != 2]
    chosen = random.sample(choices, min(base_count, len(choices)))
    # 每扇门独立 8% 概率变成精英；命中后停止，确保同层最多一扇精英。
    if allow_elite and elite_choices:
        for slot in range(len(chosen)):
            if random.random() < ELITE_GATE_CHANCE:
                chosen[slot] = random.choice(elite_choices)
                break
    special = None
    # 1-1 和 BOSS 节点本身不生成特殊门；其余节点按基础概率生成。
    allow_special = not (int(tier['tier']) == 1 and int(tier['level']) == 1)
    if allow_special and chosen and random.random() < min(1.0, SPECIAL_GATE_CHANCE + (
            max(0, effect_value(data, 4)) / 1000 if data is not None else 0)):
        # settings22 按章节给特殊事件权重；官服异常节点是额外 index=4
        # 的独立紫色能量门，不占普通门 index。
        special_event = _special_event_for_chapter(int(tier['tier']))
        if special_event is not None:
            event_type = _event_type_of_id(int(special_event['id']))
            special = route_event(tier, event_type, 0, 0,
                                  event_id=int(special_event['id']))
    result = [{'index': i + 1,
               'event': route_event(tier, int(g['event_type']), int(g.get('reward_type') or 0), i)}
              for i, g in enumerate(chosen)]
    if special is not None:
        result.append({'index': 4, 'event': special})
    return result


def _heal_all(data: dict) -> None:
    for hero in data['game']['progress']['hero_list']:
        hero['health'] = hero['max_health']


def _upgrade_artifact_candidates(data: dict) -> list[dict]:
    """升级门候选：已拥有、未满级、且专属珍宝对应角色在队。"""
    progress = data['game']['progress']
    owned = {a['id']: a['level'] for a in progress['artifact_list']}
    heroes = {h['hero_id'] for h in progress['hero_list']}
    cap_bonus = max(0, effect_value(data, 5))
    candidates = []
    for artifact in polyhedron_catalog()['artifacts']:
        if not owned.get(artifact['id']):
            continue
        if artifact['exclusive_hero_id'] and artifact['exclusive_hero_id'] not in heroes:
            continue
        limit = int(artifact['max_level']) + (cap_bonus if int(artifact['max_level']) > 1 else 0)
        if owned[artifact['id']] >= limit:
            continue
        candidates.append(artifact)
    return candidates


def _artifact_choices(data: dict, category: int = 0, upgrade: bool = False,
                      exclusive: bool | None = None) -> list[dict]:
    """普通获得门只出未拥有珍宝；升级门只出已拥有且未满级珍宝。"""
    progress = data['game']['progress']
    if upgrade:
        candidates = _upgrade_artifact_candidates(data)
        return [{'class': 5, 'params': [row['id']]}
                for row in random.sample(candidates, min(3, len(candidates)))]
    owned = {a['id']: a['level'] for a in progress['artifact_list']}
    # 专属池只认主控：官服 1-7 专属三选一全是主控角色珍宝，不能混队友专属。
    leader_id = int((data.get('game', {}).get('start_info', {}).get('leader', {}).get('hero_id')
                     or (progress.get('hero_list') or [{}])[0].get('hero_id') or 0))
    candidates = []
    for artifact in polyhedron_catalog()['artifacts']:
        if category and int(artifact.get('sub_type') or 0) != int(category):
            continue
        if artifact['exclusive_hero_id']:
            if exclusive is not True:
                continue
            if int(artifact['exclusive_hero_id']) != leader_id:
                continue
        elif exclusive is True:
            continue
        if owned.get(artifact['id']):
            # 普通获得门不能重复发已拥有珍宝；重复提升品级只走 event_type=8 升级门。
            continue
        candidates.append(artifact)
    return [{'class': 6, 'params': [row['id']]}
            for row in random.sample(candidates, min(3, len(candidates)))]


def _boss_normal_choices(data: dict, exclude_category: int = 0) -> tuple[list[dict], int]:
    """章节 BOSS 的普通珍宝轮：从 settings19 里随机取一个分类，尽量与上一轮不同。"""
    try:
        categories = [int(x) for x in config_row('settings', 19)['value']]
    except (ValueError, IndexError, TypeError):
        categories = []
    random.shuffle(categories)
    for category in categories:
        if category == int(exclude_category or 0):
            continue
        choices = _artifact_choices(data, category, exclusive=False)
        if choices:
            return choices, category
    choices = _artifact_choices(data, 0, exclusive=False)
    return choices, 0


def _boss_extra_choices(data: dict) -> list[dict]:
    """BOSS 第二段奖励：随机一个大类，抽 3 个同类普通珍宝，外加回血或喵喵币。"""
    categories = [int(x) for x in config_row('settings', 19)['value']]
    artifact_choices = []
    for category in random.sample(categories, len(categories)):
        artifact_choices = _artifact_choices(data, category, exclusive=False)
        if artifact_choices:
            break
    if not artifact_choices:
        artifact_choices = _artifact_choices(data, 0, exclusive=False)
    choices = list(artifact_choices)
    heal_effect = int(config_row('settings', 11)['value'][0])
    coin = int(config_row('settings', 9)['value'][0])
    choices.append({'class': 4, 'params': [heal_effect]})
    choices.append({'class': 1, 'params': [1, coin]})
    return choices



def _migrate_single_boss_gate(data: dict) -> None:
    """旧存档单扇 BOSS 门索引不是官服的 3，加载时改到正中。"""
    stage = data.get('game', {}).get('progress', {}).get('stage') or {}
    gates = stage.get('gate_list') or []
    if len(gates) != 1:
        return
    event_id = int(gates[0].get('event', {}).get('id') or 0)
    if event_id in (6001, 6002, 7001):
        gates[0]['index'] = 3


def _migrate_special_heal_reward(data: dict) -> None:
    """旧特殊关回血奖励是 class4/effect203，客户端固定显示 25%；迁移成 BLOOD。"""
    progress = data.get('game', {}).get('progress') or {}
    event_id = int((progress.get('event') or {}).get('id') or 0)
    if _event_type_of_id(event_id) not in SPECIAL_GATE_TYPES:
        return
    stage = progress.get('stage') or {}
    items = (stage.get('reward') or {}).get('item_list') or []
    changed = False
    converted = []
    for item in items:
        params = [int(x) for x in (item.get('params') or [])]
        if (int(item.get('class') or 0) == 4 and len(params) >= 2 and params[0] == 203):
            converted.append({'class': 7, 'params': [max(0, min(1000, params[1]))]})
            changed = True
        else:
            converted.append(item)
    if changed:
        stage['reward']['item_list'] = converted


def _migrate_upgrade_reward(data: dict) -> None:
    """旧升级门奖励错误发成 class6，客户端按“获得珍宝”画卡；迁移成 class5。"""
    progress = data.get('game', {}).get('progress') or {}
    event_id = int((progress.get('event') or {}).get('id') or 0)
    event = next((row for row in polyhedron_catalog()['events'] if row['id'] == event_id), None)
    if event is None or int(event['event_type']) != 8:
        return
    stage = progress.get('stage') or {}
    items = (stage.get('reward') or {}).get('item_list') or []
    changed = False
    converted = []
    for item in items:
        if int(item.get('class') or 0) == 6:
            converted.append({'class': 5, 'params': list(item.get('params') or [])})
            changed = True
        else:
            converted.append(item)
    if changed:
        stage['reward']['item_list'] = converted


def _migrate_attribute_reward(data: dict) -> None:
    """旧属性门把 5%/10%/医疗包三选一一起发；统一收敛为单个 +10% 打字机。"""
    progress = data.get('game', {}).get('progress') or {}
    event_id = int((progress.get('event') or {}).get('id') or 0)
    event = next((row for row in polyhedron_catalog()['events'] if row['id'] == event_id), None)
    if event is None or int(event['event_type']) != 4:
        return
    stage = progress.get('stage') or {}
    if int(stage.get('save_point') or 0) != 2:
        return
    items = (stage.get('reward') or {}).get('item_list') or []
    if items != [{'class': 4, 'params': [202]}]:
        stage['reward']['item_list'] = [{'class': 4, 'params': [202]}]


def _migrate_shop_stock(data: dict) -> None:
    """旧商店可能抽到专属珍宝、5%打字机或医疗包商品；加载时整店重抽。"""
    shop = (data.get('game', {}).get('progress', {}).get('stage') or {}).get('shop')
    if not isinstance(shop, dict):
        return
    shop.setdefault('recover_times', 0)
    shop.setdefault('refresh_times', 0)
    invalid = False
    for entry in shop.get('item_list') or []:
        item = entry.get('item') or {}
        kind = int(item.get('class') or 0)
        params = [int(x) for x in (item.get('params') or [])]
        if kind in (5, 6) and params:
            artifact = next((a for a in polyhedron_catalog()['artifacts']
                             if int(a['id']) == params[0]), None)
            if artifact is None or int(artifact.get('exclusive_hero_id') or 0):
                invalid = True
                break
        elif kind == 4 and params and params[0] == 202:
            continue
        else:
            invalid = True
            break
    if invalid:
        from .polyhedron_shop import shop_stock
        shop['item_list'] = shop_stock(data)


def _migrate_recruit_stage(data: dict) -> None:
    """修复旧存档的招募关：reward_type、场景参数及旧的三人奖励页。"""
    progress = data.get('game', {}).get('progress') or {}
    event = progress.get('event') or {}
    stage = progress.get('stage') or {}
    event_id = int(event.get('id') or 0)
    event_row = next((row for row in polyhedron_catalog()['events']
                      if int(row['id']) == event_id), None)
    if event_row is None or int(event_row['event_type']) != 2:
        return
    changed = False
    if int(event.get('reward_type') or 0) != 2:
        event['reward_type'] = 2
        changed = True
    save_point = int(stage.get('save_point') or 0)
    if save_point >= 3:
        # 招募已完成，save_point=3 的 params 是下一层 ID，不能再当场景参数。
        return
    params = [int(x) for x in stage.get('params') or []]
    valid_types = {1, 2, 3}
    params_ok = (len(params) >= 2 and len(params) % 2 == 0
                 and all(params[i+1] in valid_types for i in range(0, len(params), 2)))
    reward_items = ((stage.get('reward') or {}).get('item_list') or [])
    selected_ok = False
    if save_point == 2 and len(reward_items) == 1:
        item = reward_items[0]
        item_params = [int(x) for x in (item.get('params') or [])] if isinstance(item, dict) else []
        selected_ok = (int(item.get('class') or 0) == 3 and len(item_params) >= 2
                       and item_params[1] in valid_types)
    if not params_ok:
        stage['params'] = _recruit_candidate_params(data)
        changed = True
    if save_point >= 2 and not selected_ok:
        # 旧代码在招募场景结束直接生成了三个单参数 class=3 候选，客户端会画出
        # 三张角色卡但无法选中/确认。清回场景保存点，等待玩家重新交互。
        stage['save_point'] = 1
        stage['reward'] = {'round': 0, 'item_list': []}
        changed = True
    elif save_point == 1 and reward_items:
        stage['reward'] = {'round': 0, 'item_list': []}
        changed = True


def normalize_data(data: dict) -> dict:
    """按客户端 SC 描述补齐必填值和空列表，使存档与推送具有相同结构。"""
    _migrate_single_boss_gate(data)
    _migrate_special_heal_reward(data)
    _migrate_upgrade_reward(data)
    _migrate_attribute_reward(data)
    _migrate_shop_stock(data)
    _migrate_recruit_stage(data)
    refresh_attribute_list(data)
    return decode_message(18001, encode_message(18001, data), 'sc')


def polyhedron_data(user: dict) -> dict:
    """恢复完整多维状态；新账号从可用主题的未开始状态初始化。"""
    from .polyhedron_heroes import sync_unlocked_heroes
    if user.get('polyhedron'):
        return sync_unlocked_heroes(user, normalize_data(deepcopy(user['polyhedron'])))
    from .challenge_data import challenge_catalog
    shelf = next(r for r in challenge_catalog()['polyhedron'] if r['activity_id'] == 4407302)
    return sync_unlocked_heroes(user, normalize_data({'activity_id': shelf['activity_id'], 'game': {
        'state': 1, 'start_info': {'leader': {'hero_id': shelf['leader_hero_id'][0]}, 'difficulty': 1},
        'progress': {'tier_id': 1001, 'event': {}, 'stage': {'reward': {}}, 'cooperate_unique_skill_id': 0}},
        'decision': {}, 'terminal': {}, 'manual': {}, 'is_new': 0,
        'unlocked_hero_list': [{'hero_id': i} for i in sorted(set(shelf['leader_hero_id']+shelf['polyhedron_hero_id']))]}))


def coin_value(progress: dict) -> int:
    row = next((r for r in progress.get('stackable_item_list') or [] if r['id'] == 1), None)
    return int(row['num']) if row else 0


def coin_change(progress: dict, amount: int) -> None:
    """局内金币保存于路线库存，不改变局外货币余额。"""
    rows = progress['stackable_item_list']
    row = next((r for r in rows if r['id'] == 1), None)
    if row is None:
        row = {'id': 1, 'num': 0};rows.append(row)
    if row['num']+amount < 0:
        raise ValueError('多维局内金币不足')
    row['num'] += amount


def update_max_health(data: dict) -> None:
    """按客户端原始公式重算最大生命，保留当前受伤量。"""
    progress = data['game']['progress']
    leader = data['game']['start_info']['leader']
    for hero in progress['hero_list']:
        template = config_row('heroes', hero['hero_id'])['standard_id']
        astrolabes = leader['astrolabe_list'] if hero['hero_id'] == leader['hero_id'] else next(
            h['astrolabe_id_list'] for h in data['unlocked_hero_list'] if h['hero_id'] == hero['hero_id'])
        maximum = polyhedron_attributes(template, astrolabes or [], progress['effect_list'],
            hero['difference_attribute_list'], len(progress['artifact_list']))[2]
        hero['health'] = max(0, maximum-max(0, hero['max_health']-hero['health']))
        hero['max_health'] = maximum


def add_hero(data: dict, hero_id: int) -> None:
    """按多维专用模板招募角色，并加入有空位的出战队伍。"""
    progress = data['game']['progress']
    template = config_row('heroes', hero_id)['standard_id']
    if any(h['hero_id'] == hero_id for h in progress['hero_list']):
        raise ValueError('多维角色已经招募')
    progress['hero_list'].append({'hero_id': hero_id, 'template_id': template, 'health': 0,
        'max_health': 0, 'reborn_cold_down': 0, 'difference_attribute_list': [], 'injured': 0, 'heal': 0, 'damage': 0})
    update_max_health(data)
    if len(progress['fight_hero_id_list']) < 3:
        progress['fight_hero_id_list'].append(hero_id)


def apply_item(data: dict, item: dict) -> None:
    """将奖励、商店物品或效果写入局内状态，再重算角色生命值。"""
    progress = data['game']['progress']
    kind, params = item['class'], item['params']
    if kind == 1:
        coin_change(progress, params[-1])
    elif kind == 7:
        # 客户端 PolyhedronConst.ITEM_TYPE.BLOOD = 7：params[0] 是千分比，
        # 600 表示全队恢复 60%。动态回血必须用这个类型，不能塞固定效果 203。
        heal_permille = max(0, min(1000, int(params[0] if params else 0)))
        for hero in progress['hero_list']:
            if hero['health'] > 0:
                hero['health'] = min(hero['max_health'],
                                     hero['health']+hero['max_health']*heal_permille//1000)
    elif kind == 3:
        add_hero(data, params[0])
    elif kind in (5, 6):
        # class5=ARTIFACT_UP_LEVEL（已拥有珍宝 +1 级），class6=ARTIFACT（获得）。
        # 两者最终都写 artifact_list；class5 必须是已拥有且未满级，客户端会显示 LV.x→LV.x+1。
        cfg = config_row('artifacts', params[0])
        max_level = _artifact_max_level(data, cfg)
        existing = next((a for a in progress['artifact_list'] if a['id'] == cfg['id']), None)
        if existing:
            existing['level'] = min(existing['level']+1, max_level)
        else:
            row = {'id': cfg['id'], 'level': 1, 'life_time': 0}
            progress['artifact_list'].append(row)
            # 信标 3 / 终端 3004、3010：新获得珍宝时按 key=6 的概率直接升 1 级。
            chance = min(1000, max(0, effect_value(data, 6)))
            if max_level > 1 and chance and random.randrange(1000) < chance:
                row['level'] = min(2, max_level)
        manual = data['manual']['sample_list']
        row = next((r for r in manual if r['id'] == cfg['id']), None)
        if row:
            row['state'] = 1
        else:
            manual.append({'id': cfg['id'], 'state': 1})
    elif kind == 4:
        effect = config_row('effects', params[0])
        if effect['action'] == 3:
            # 特殊挑战的回血道具用 params[1] 覆盖效果百分比（如 580=58%）；
            # 普通医疗包没有第二参数，继续使用 effect.params[0]。
            heal_permille = int(params[1]) if len(params) > 1 else int(effect['params'][0])
            for hero in progress['hero_list']:
                if hero['health'] > 0:
                    hero['health'] = min(hero['max_health'],
                                         hero['health']+hero['max_health']*heal_permille//1000)
        elif effect['action'] == 6:
            for _, amount in effect['params']:
                coin_change(progress, amount)
        elif effect['moment'] == 1 and effect['action'] == 2:
            for hero in progress['hero_list']:
                values = {r['id']: r['value'] for r in hero['difference_attribute_list']}
                for attr, amount in effect['params']:
                    values[attr] = values.get(attr, 0)+amount
                hero['difference_attribute_list'] = [{'id': i, 'value': n} for i, n in values.items()]
        elif not any(e['id'] == effect['id'] for e in progress['effect_list']):
            progress['effect_list'].append({'id': effect['id'], 'life_time': 0})
    else:
        raise ValueError('多维物品类型无效')
    update_max_health(data)


def _nested_health_nodes(value, party: set[int], depth: int = 0) -> list[dict]:
    """在未知战报字段里寻找“角色ID/当前生命/最大生命”三元组。

    原生战报的已知 characters 列表通常只带玩家队伍；A-02 这类场景 NPC
    如果被放进未知的嵌套字段，就靠这里兜底识别，避免永远按满血算。
    """
    found: list[dict] = []
    if depth > 4 or not isinstance(value, (dict, list)):
        return found
    nodes = value.values() if isinstance(value, dict) else value
    for node in nodes:
        if isinstance(node, dict):
            def _first(*keys):
                for key in keys:
                    raw = node.get(key)
                    if isinstance(raw, list):
                        raw = raw[0] if raw else 0
                    if raw not in (None, ''):
                        return raw
                return 0

            try:
                identifier = int(_first('1', 'hero_id'))
                current = int(_first('2', 'current', 'health'))
                maximum = int(_first('3', 'max', 'max_health'))
            except (TypeError, ValueError):
                identifier = current = maximum = 0
            if identifier > 0 and maximum > 0 and current >= 0:
                found.append({'id': identifier, 'current': min(current, maximum), 'max': maximum})
            else:
                found.extend(_nested_health_nodes(node, party, depth + 1))
        elif isinstance(node, list):
            found.extend(_nested_health_nodes(node, party, depth + 1))
    return [x for x in found if x['id'] not in party]


def _special_robot_health(report: dict | None, progress: dict) -> dict | None:
    """从原生战报找出 A-02 的剩余/最大生命；NPC 也在 characters 列表中。"""
    if not report:
        return None
    info = report.get('info') or {}
    characters = info.get('characters') or []
    party = {int(h.get('hero_id') or 0) for h in progress.get('hero_list') or []}
    unmatched = []
    for character in characters:
        identifier = int(character.get('1') or 0)
        maximum = int(character.get('3') or 0)
        if maximum <= 0:
            continue
        if identifier in SPECIAL_ROBOT_IDS:
            current = max(0, min(maximum, int(character.get('2') or 0)))
            return {'id': identifier, 'current': current, 'max': maximum,
                    'ratio_permille': current * 1000 // maximum}
        if identifier not in party:
            unmatched.append(character)
    # 兼容只上报一个非队伍 NPC 的战报；多于一个时不猜。
    if len(unmatched) == 1:
        character = unmatched[0]
        maximum = int(character.get('3') or 0)
        current = max(0, min(maximum, int(character.get('2') or 0)))
        return {'id': int(character.get('1') or 0), 'current': current, 'max': maximum,
                'ratio_permille': current * 1000 // maximum}
    nested = _nested_health_nodes({k: v for k, v in info.items() if k != 'characters'}, party)
    known = [x for x in nested if x['id'] in SPECIAL_ROBOT_IDS]
    if known:
        node = known[0]
        return {'id': node['id'], 'current': node['current'], 'max': node['max'],
                'ratio_permille': node['current'] * 1000 // node['max']}
    if len(nested) == 1:
        node = nested[0]
        return {'id': node['id'], 'current': node['current'], 'max': node['max'],
                'ratio_permille': node['current'] * 1000 // node['max']}
    return None


def _a02_reward_values(robot: dict | None) -> tuple[int, int, int | None]:
    """A-02 奖励：金币满 600 每掉 1% 减 10；回血满 60% 每掉 1% 减 1%。"""
    if not robot:
        # 当前原生战报没有 A-02 槽位时，成功完成保护关按满血兜底，
        # 不能让 100% 的画面只拿到 100 金币。
        return 600, 60, None
    maximum = max(1, int(robot.get('max') or 0))
    current = max(0, min(maximum, int(robot.get('current') or 0)))
    health_percent = current * 100 // maximum
    lost_percent = max(0, 100 - health_percent)
    return max(0, 600 - 10 * lost_percent), max(0, 60 - lost_percent), health_percent


def _recruit_candidate_params(data: dict) -> list[int]:
    """按官服 recruit 场景格式返回三组“角色ID、enlist_type”参数。

    客户端 PolyhedronDataTemplate.GetEnlistHeroList 将 stage.params 按两两
    解析为 (heroId, enlist_type)，enlist_type 取值来自 PolyhedronConst：
    1=BATTLE（战斗考验）、2=PAY（800 金币聘请）、3=FREE（免费邀请）。
    第二项不是站位；若误当成站位，客户端会把 1/2/3 当成三种交互类型，
    虽然偶尔能显示三张卡，但奖励确认无法正确选中。
    """
    progress = data.get('game', {}).get('progress') or {}
    event = progress.get('event') or {}
    stage = progress.get('stage') or {}
    owned = {int(h.get('hero_id') or 0) for h in progress.get('hero_list') or []}
    unlocked = []
    for row in data.get('unlocked_hero_list') or []:
        hero_id = int(row.get('hero_id') or 0) if isinstance(row, dict) else int(row or 0)
        if hero_id > 0 and hero_id not in owned and hero_id not in unlocked:
            unlocked.append(hero_id)
    # 同一招募节点必须每次生成同一组候选。登录下发 18001 和随后 cs_18020
    # 都会分别在副本上跑迁移；若这里用全局随机，客户端看到的候选与 18020
    # 校验时重新生成的候选不一致，会报“招募候选不存在”。以节点和已解锁角色
    # 作为稳定种子，保证断线重连也不换人。
    seed_text = '|'.join(str(x) for x in (
        data.get('activity_id') or 0, progress.get('tier_id') or 0,
        event.get('id') or 0, stage.get('stage_id') or 0, tuple(sorted(unlocked))))
    rng = random.Random(seed_text)
    hero_cfg = {int(row['id']): row for row in polyhedron_catalog()['heroes']}
    battle_pool = [hero_id for hero_id in unlocked
                   if int((hero_cfg.get(hero_id) or {}).get('target_enemy') or 0) > 0]
    # 官方样例固定一局三种交互各一个：PAY / FREE / BATTLE。
    # BATTLE 必须选带 trial 敌人的角色，否则客户端会进入没有目标的战斗。
    chosen: list[tuple[int, int]] = []
    used: set[int] = set()
    if battle_pool:
        battle_id = rng.choice(battle_pool)
        chosen.append((battle_id, 1))
        used.add(battle_id)
    rest = [hero_id for hero_id in unlocked if hero_id not in used]
    rng.shuffle(rest)
    if rest:
        pay_id = rest.pop()
        chosen.append((pay_id, 2))
        used.add(pay_id)
    if rest:
        free_id = rest.pop()
        chosen.append((free_id, 3))
        used.add(free_id)
    # 解锁角色不足三人时用剩余任意角色补足 FREE，仍保证场景不是单 NPC。
    for hero_id in unlocked:
        if len(chosen) >= 3:
            break
        if hero_id not in used:
            chosen.append((hero_id, 3))
            used.add(hero_id)
    rng.shuffle(chosen)
    params: list[int] = []
    for hero_id, enlist_type in chosen:
        params.extend([hero_id, enlist_type])
    return params



def _apply_hero_battle_stats(progress: dict, report: dict) -> None:
    """把原生战报的角色有效伤害与生命变化累计到局内统计栏。

    官服 characters 字段：1=角色 ID、2=剩余生命、3=最大生命、4=本场有效伤害。
    官服最终 18001 hero_list 的 damage 是整局累计值，最终战斗统计页直接读这三个字段。
    """
    for character in (report.get('info') or {}).get('characters') or []:
        if not isinstance(character, dict):
            continue
        identifier = int(character.get('1') or 0)
        maximum = int(character.get('3') or 0)
        if identifier <= 0 or maximum <= 0:
            continue
        hero = next((h for h in progress.get('hero_list') or []
                     if int(h.get('hero_id') or 0) == identifier), None)
        if hero is None:
            continue
        previous = max(0, int(hero.get('health') or 0))
        current = max(0, min(maximum, int(character.get('2') or 0)))
        scaled = current*int(hero.get('max_health') or 0)//maximum
        dealt = max(0, int(character.get('4') or 0))
        hero['damage'] = max(0, int(hero.get('damage') or 0))+dealt
        if scaled < previous:
            hero['injured'] = max(0, int(hero.get('injured') or 0))+previous-scaled
        elif scaled > previous:
            hero['heal'] = max(0, int(hero.get('heal') or 0))+scaled-previous
        hero['health'] = scaled


def _previous_tier(tier_id: int) -> dict | None:
    tiers = polyhedron_catalog()['tiers']
    index = _tier_index(tier_id)
    return tiers[index - 1] if index > 0 else None


def _restore_special_gate(data: dict, user: dict, legacy: bool = False) -> bool:
    """特殊关结束回到进入前的节点，保留原来的普通门，不把层数推进到下一关。"""
    saved = user.pop(SPECIAL_RETURN_KEY, None)
    if not saved:
        if not legacy:
            return False
        # 旧存档没有进入前快照：旧代码已经把 tier 推到下一关。
        # 回退一层并重新生成下一关的门，同时剔除刚刚完成的特殊门。
        progress = data['game']['progress']
        target_tier = int(progress['tier_id'])
        previous = _previous_tier(target_tier)
        if previous is None:
            return False
        progress['tier_id'] = int(previous['id'])
        normal_event = route_event(previous, 1)
        progress['event'] = normal_event
        open_gates(data, target_tier)
        progress['stage']['stage_id'] = normal_event['stage_id']
        progress['stage']['gate_list'] = [
            g for g in progress['stage']['gate_list']
            if _event_type_of_id(int(g['event']['id'])) not in SPECIAL_GATE_TYPES]
        return True
    progress = data['game']['progress']
    progress['tier_id'] = int(saved['tier_id'])
    progress['event'] = deepcopy(saved['event'])
    stage = deepcopy(saved['stage'])
    gate_index = int(saved.get('gate_index') or 0)
    stage['gate_list'] = [g for g in stage.get('gate_list') or []
                          if int(g.get('index') or 0) != gate_index]
    stage['save_point'] = 3
    stage['reward'] = {'round': 0, 'item_list': []}
    stage['attribute_modify_list'] = []
    progress['stage'] = stage
    return True


def open_stage(data: dict, event: dict) -> None:
    """进入实际战斗保存点，清除上一个节点的待选奖励与门。"""
    progress = data['game']['progress']
    previous = progress.get('stage') or {}
    previous_gates = deepcopy(previous.get('gate_list') or [])
    progress['event'] = deepcopy(event)
    progress['stage'] = {'stage_id': event['stage_id'], 'save_point': 1, 'reward': {'round': 0, 'item_list': []},
        'params': [], 'gate_list': [], 'attribute_modify_list': []}
    event_row = next((row for row in polyhedron_catalog()['events']
                      if int(row['id']) == int(event['id'])), None)
    if event_row is not None and int(event_row['event_type']) == 2:
        # 官服 recruit 场景：params 是 [角色ID, enlist_type, ...]。
        # 招募场景内的“放弃招募”只走客户端 DoPolyhedronNextTrigger，不发 18020；
        # 保留进入本节点时已有的门，玩家放弃后仍能回到路线继续选择。
        progress['stage']['gate_list'] = previous_gates
        progress['stage']['params'] = _recruit_candidate_params(data)


def open_gates(data: dict, tier_id: int | None = None) -> None:
    """按下一节点的真实门配置推进；章节末只给 BOSS 门。"""
    progress = data['game']['progress']
    tiers = polyhedron_catalog()['tiers']
    if tier_id is None:
        next_tier = _next_tier(progress['tier_id'])
    else:
        next_tier = next((t for t in tiers if t['id'] == tier_id), None)
    if next_tier is None:
        data['game']['state'] = 3
        return
    progress['stage']['save_point'] = 3
    progress['stage']['params'] = [int(next_tier['id'])]
    progress['stage']['gate_list'] = _gate_events(
        next_tier, polyhedron_catalog()['gates'], _lone_wolf(data), data)


def make_rewards(data: dict, user: dict | None = None,
                 special_result: dict | None = None) -> None:
    """按真实节点类型生成奖励；章节 BOSS 恢复全队并进入下一章。"""
    progress = data['game']['progress']
    event = next((row for row in polyhedron_catalog()['events']
                  if row['id'] == progress['event']['id']), None)
    if event is None:
        raise ValueError('多维事件配置不存在')
    event_type = int(event['event_type'])
    category = int(progress['event'].get('reward_type') or 0)
    if event_type == 5:
        from .polyhedron_shop import open_shop
        open_shop(data)
        open_gates(data)
        return
    if event_type == 7:
        # 终章 BOSS 结算：先处理 BOSS 后金币增值与珍宝升级，再结束整局。
        boss_coin_bonus = max(0, effect_value(data, 19))
        if boss_coin_bonus:
            coin_change(progress, coin_value(progress)*boss_coin_bonus//1000)
        if effect_value(data, 21) > 0:
            _upgrade_random_artifact(data)
        if user is not None:
            polyhedron_end(user, data, True)
        else:
            data['game']['state'] = 3
        return
    if event_type == 6:
        # 1-7 / 2-8 章节 BOSS：先处理 BOSS 后的金币增值与珍宝升级，再进入
        # 普通珍宝 → 医疗包 → 普通珍宝 → 专属三选一的连续结算。
        boss_coin_bonus = max(0, effect_value(data, 19))
        if boss_coin_bonus:
            coin_change(progress, coin_value(progress)*boss_coin_bonus//1000)
        if effect_value(data, 21) > 0:
            _upgrade_random_artifact(data)
        choices, boss_category = _boss_normal_choices(data)
        if not choices:
            choices = _artifact_choices(data, 0, exclusive=False)
        progress['stage']['params'] = [int(boss_category)]
    elif event_type == 9:
        # 9002 只在终端 3101「战前整备」生效时出现，给队长专属三选一；
        # 默认 9001 是先随机一个大类，再只从该大类出三件普通珍宝。
        if int(event['id']) == 9002:
            choices = _artifact_choices(data, 3007, exclusive=True)
            if not choices:
                choices, fallback_category = _boss_normal_choices(data)
                progress['stage']['params'] = [int(fallback_category)]
        else:
            choices, initial_category = _boss_normal_choices(data)
            if not choices:
                choices = _artifact_choices(data, 0, exclusive=False)
            progress['stage']['params'] = [int(initial_category)]
    elif event_type in (3, 3002):
        choices = _artifact_choices(data, category)
    elif event_type == 8:
        # 升级门的 gate reward_type=7 是门面图标/说明分类，不是珍宝 sub_type；
        # 实际升级池是全部已拥有且未满级的珍宝。
        choices = _artifact_choices(data, 0, upgrade=True)
    elif event_type in (1, 1002):
        # 普通货币/战斗奖励；终端 3101 的专属效果只作用于 1-1 的 9002 整备奖励，
        # 不能把第一场真实战斗的奖励也替换成专属。
        amount = int(event['params'][0]) + max(0, effect_value(data, 9))
        choices = [{'class': 1, 'params': [1, amount]}]
    elif event_type == 4:
        # 角色属性门（兔兔打字机）只有 +10% 生命/+10% 攻击一个选项，
        # 不能生成 5% 版本，也不能混医疗包。
        choices = [{'class': 4, 'params': [202]}]
    elif event_type == 2:
        # 招募候选只在场景内由 18020 选定；这里若生成三选一会把
        # 场景 UI 覆盖成奖励页，并导致客户端无法点选 NPC。
        progress['stage']['save_point'] = 1
        progress['stage']['reward'] = {'round': 0, 'item_list': []}
        return
    elif event_type in SPECIAL_GATE_TYPES:
        result_info = special_result or {}
        event_id = int(event['id'])
        mode = _special_mode(event_id)
        kills = max(0, min(60, int(result_info.get('kills') or 0)))
        waves = min(10, kills // 6)
        battle_ms = max(0, int(result_info.get('battle_ms') or 0))
        remaining = max(0, min(60, 60 - battle_ms // 1000))
        elapsed = 60 - remaining
        robot = result_info.get('robot_health') or {}
        ratio = max(0, min(1000, int(robot.get('ratio_permille') or 0))) if robot else None
        coin_reward = int(str(event_id)[-1]) in (1, 3)
        if mode == 'a02':
            # 保护 A-02：金币满值 600，A-02 每掉 1% 扣 10；
            # 回血满值 60%，每掉 1% 扣 1%。
            coin_amount, heal_percent, health_percent = _a02_reward_values(robot)
            if coin_reward:
                choices = [{'class': 1, 'params': [1, coin_amount]}]
            else:
                # 动态回血用 BLOOD 类型，客户端按 params[0] 千分比显示百分比；
                # 旧写法 class=4/effect203 会被客户端固定显示成“医疗包 25%”。
                choices = [{'class': 7, 'params': [heal_percent * 10]}]
        elif mode == 'time':
            # 一波 7 只、1 分钟计时：按剩余时间给，0:55 → 450 金币。
            if coin_reward:
                amount = max(100, min(600, 600 - 30 * elapsed))
                choices = [{'class': 1, 'params': [1, amount]}]
            else:
                heal_percent = min(58, (58 * remaining + 30) // 60)
                choices = [{'class': 7, 'params': [heal_percent * 10]}]
        else:
            # 十波 60 只击杀挑战：按完整波次/击杀数结算。
            if coin_reward:
                amount = max(100, 600 - (10 - waves) * 50)
                choices = [{'class': 1, 'params': [1, amount]}]
            else:
                heal_percent = min(58, kills)
                choices = [{'class': 7, 'params': [heal_percent * 10]}]
    else:
        # 兜底属性奖励同样只允许 +10% 兔兔打字机。
        choices = [{'class': 4, 'params': [202]}]
    progress['stage']['save_point'] = 2
    progress['stage']['reward'] = {'round': 1, 'item_list': choices}
    if not choices:
        # 1-1 整备奖励天然没有候选时直接进入下一层的门；异常节点则留在同一层。
        open_gates(data, progress['tier_id'] if event_type in SPECIAL_GATE_TYPES else None)



def polyhedron_end(user: dict, data: dict, success: bool) -> None:
    """一局仅结算一次；经验按官服实测的战斗层数计，point 暂保留旧近似。"""
    data['game']['state'] = 3
    if user.get('polyhedron_run_settled'):
        return
    user['polyhedron_run_settled'] = True
    completed = user.get('polyhedron_completed_nodes', 0)
    difficulty = data['game']['start_info']['difficulty']
    multiplier = config_row('difficulties', difficulty)['score']
    # 官服 sc_18005 实测：decision_exp=N×16，terminal_exp=N×220；这里必须分开算、分开发。
    # 赏金猎人（effect 114，key=33）按千分比同时放大两种经验，当前配置为 +50%。
    exp_factor = 1000 + max(0, effect_value(data, 33))
    decision_exp = completed*DECISION_EXP_PER_BATTLE*exp_factor//1000
    terminal_exp = completed*TERMINAL_EXP_PER_BATTLE*exp_factor//1000
    # 官服 point 公式尚未拆出：同为 N=3 的两次 1-3 分别为 205370/207480。
    # 保留旧近似仅用于本地兼容，不能把它当成官服公式。
    value = completed*75000*multiplier//1000
    user['polyhedron_settlement'] = {'point': value, 'decision_exp': decision_exp, 'terminal_exp': terminal_exp}
    if success and difficulty not in data['clear_difficulty_list']:
        data['clear_difficulty_list'].append(difficulty)
    if success:
        leader = str(data['game']['start_info']['leader']['hero_id'])
        clears = user.setdefault('polyhedron_leader_clears', {})
        clears[leader] = clears.get(leader, 0) + 1
    if decision_exp or terminal_exp:
        grant_rewards(user, [{'id': 45, 'num': decision_exp}, {'id': 46, 'num': terminal_exp}])


def settle_polyhedron(user: dict, battle: dict) -> None:
    """真实战斗结果更新局内剩余生命和保存点，重复结算由战斗会话拦截。"""
    data = polyhedron_data(user)
    progress = data['game']['progress']
    report = battle['reported']
    event_id = int(progress['event'].get('id') or 0)
    prep_room = (int(battle['common'].get('dest') or 0) == 3035199
                 and event_id in (9001, 9002))
    if prep_room and report is None:
        # 3035199 没有敌人，客户端可能只上报 54032 完成整备房间，不带战斗战果。
        report = {'result': 1, 'info': {}}
        battle['reported'] = report
    _apply_hero_battle_stats(progress, report)
    if report['result'] == 1:
        event_id = int(progress['event'].get('id') or 0)
        event_row = next((e for e in polyhedron_catalog()['events'] if int(e['id']) == event_id), None)
        special_result = None
        if event_row and int(event_row['event_type']) in SPECIAL_GATE_TYPES:
            # native 战报字段 11 是已击退敌人数；1 分钟挑战最多 10 波 × 6 只。
            info = report.get('info') or {}
            kills = max(0, min(60, int(info.get('11') or 0)))
            robot = _special_robot_health(report, progress)
            special_result = {'kills': kills,
                              'battle_ms': max(0, int(info.get('2') or 0)),
                              'robot_health': robot}
        if event_row and int(event_row['event_type']) == 2:
            # 招募关是场景内交互，不是战斗奖励。54032 有两种含义：
            # 1) 已通过 18020 选中候选（save_point=2、reward 有单人项）：
            #    保留奖励页等待 18012 确认；
            # 2) 玩家在交互界面点了“放弃招募”（save_point 仍为 1）：
            #    客户端只发 54032，不会发 18020/18012，服务端要开门推进路线。
            stage = progress['stage']
            selected = (int(stage.get('save_point') or 0) == 2
                        and bool((stage.get('reward') or {}).get('item_list')))
            if not selected:
                stage['reward'] = {'round': 0, 'item_list': []}
                open_gates(data)
                user['polyhedron_completed_nodes'] = user.get('polyhedron_completed_nodes', 0) + 1
            user['polyhedron'] = normalize_data(data)
            return
        # “每关结束”类终端：key27 回血、key26 额外金币，异常节点也算一次结束。
        heal_rate = max(0, effect_value(data, 27))
        if heal_rate:
            for hero in progress['hero_list']:
                if hero['health'] > 0:
                    hero['health'] = min(hero['max_health'], hero['health']+hero['max_health']*heal_rate//1000)
        coin_change(progress, max(0, effect_value(data, 26)))
        # 异常节点 10001/10002 的 gate 描述明确“不计入关卡层数”，只发奖励不加 N。
        # 3035199 的 1-1 整备房间不是战斗层，也不加 N、不重新抽奖励。
        if prep_room:
            pass
        elif not event_row or int(event_row['event_type']) not in SPECIAL_GATE_TYPES:
            user['polyhedron_completed_nodes'] = user.get('polyhedron_completed_nodes', 0)+1
        # 终端 9003：每经过 key13 个关卡，随机提升一个记忆珍宝。
        interval = max(0, effect_value(data, 13))
        completed = int(user.get('polyhedron_completed_nodes', 0))
        if not prep_room and interval and completed and completed % interval == 0:
            _upgrade_random_artifact(data)
        if not prep_room:
            make_rewards(data, user, special_result)
    elif report['result'] == 2:
        polyhedron_end(user, data, False)
    user['polyhedron'] = normalize_data(data)


def polyhedron_request(user: dict, command: int, request: dict) -> dict:
    """执行开局、选奖励、选门、换队和成长选择，状态先推送再返回请求结果。"""
    data = polyhedron_data(user)
    game, progress = data['game'], data['game']['progress']
    if command == 18010:
        if game['state'] == 2:
            raise ValueError('已有进行中的多维路线')
        heroes = request['hero_id_list']
        available = {h['hero_id'] for h in data['unlocked_hero_list']}
        if not heroes or len(heroes) > 3 or len(set(heroes)) != len(heroes) or not set(heroes) <= available:
            raise ValueError('多维队伍无效')
        difficulty = config_row('difficulties', request['difficulty'])
        if difficulty['unlock_difficulty'] and difficulty['unlock_difficulty'] not in data['clear_difficulty_list']:
            raise ValueError('多维难度前置尚未通关')
        if len(set(request['beacon_id_list'])) != len(request['beacon_id_list']):
            raise ValueError('多维信标重复')
        if not set(request['beacon_id_list']) <= set(data['beacon_id_list']):
            raise ValueError('多维信标未解锁')
        from .polyhedron_heroes import leader_snapshot
        astrolabes = next(h['astrolabe_id_list'] for h in data['unlocked_hero_list'] if h['hero_id'] == heroes[0])
        leader = leader_snapshot(user, heroes[0], astrolabes)
        game['start_info'] = {'leader': leader, 'difficulty': request['difficulty'],
            'beacon_id_list': request['beacon_id_list'], 'terminal_id_list': data['terminal']['upgrade_id_list']}
        game['state'] = 2
        user.pop(SPECIAL_RETURN_KEY, None)
        progress.update({'tier_id': polyhedron_catalog()['tiers'][0]['id'], 'hero_list': [],
            'fight_hero_id_list': [], 'effect_list': [], 'artifact_list': [], 'attribute_list': [],
            'stackable_item_list': [], 'cooperate_unique_skill_id': 0})
        for hero in heroes:
            add_hero(data, hero)
        for beacon in request['beacon_id_list']:
            for effect in config_row('beacons', beacon)['effect_id_list']:
                apply_item(data, {'class': 4, 'params': [effect]})
        for talent in data['terminal']['upgrade_id_list']:
            apply_item(data, {'class': 4, 'params': [config_row('terminal', talent)['effect_id']]})
        first_tier = polyhedron_catalog()['tiers'][0]
        # 官服 1-1 是「战前整备」非战斗奖励：9002 专属珍宝（终端 3101）或 9001
        # 普通首关珍宝，固定停在 stage 3035199 / save_point=2；首战从 1-2 开始。
        initial_id = 9002 if effect_value(data, 22) > 0 else 9001
        initial = route_event(first_tier, 9, 3007 if initial_id == 9002 else 0,
                              event_id=initial_id)
        open_stage(data, initial)
        make_rewards(data, user)
        user['polyhedron_completed_nodes'] = 0
        user['polyhedron_run_settled'] = False
    elif command in (18016, 18018):
        if command == 18018 and game['state'] == 2:
            polyhedron_end(user, data, False)
        else:
            game['state'] = 1
    elif command == 18034:
        data['is_new'] = 0
    elif command == 66002:
        data['terminal']['upgrade_id_list'] = []
        data['terminal']['reset_times'] += 1
    elif command == 66004:
        row = next((h for h in data['unlocked_hero_list'] if h['hero_id'] == request['hero_id']), None)
        if row is None:
            raise ValueError('多维角色未解锁')
        owned = next((h for h in user['heroes'] if h['id'] == request['hero_id']), None)
        ids = request['astrolabe_id_list']
        if not owned or len(ids) > 3 or len(set(ids)) != len(ids) or not set(ids) <= set(owned['astrolabes']):
            raise ValueError('多维神格配置无效')
        row['astrolabe_id_list'] = list(ids)
    elif command == 18032:
        ids = request['upgrade_id_list']
        if len(set(ids)) != len(ids):
            raise ValueError('多维天赋重复')
        rows = [config_row('terminal', i) for i in ids]
        levels = polyhedron_catalog()['terminal_levels']
        level = max((c for c in levels if c['exp'] <= build_currency_balances(user)[46]), key=lambda c: c['id'])
        if sum(c['cost'] for c in rows) > level['point'] or any(c['need_level'] > level['id'] for c in rows):
            raise ValueError('多维天赋点或等级不足')
        if any(0 not in c['pre_id_list'] and not set(c['pre_id_list']) & set(ids) for c in rows):
            raise ValueError('多维天赋前置条件不足')
        data['terminal']['upgrade_id_list'] = list(ids)
    elif command in (66034, 66036):
        if command == 66034:
            item = request['beacon_id'];config_row('beacons', item)
            if item in data['beacon_id_list']:
                raise ValueError('信标已解锁')
            data['beacon_id_list'].append(item)
        else:
            item = request['hero_id'];config_row('heroes', item)
            if any(h['hero_id'] == item for h in data['unlocked_hero_list']):
                raise ValueError('角色已解锁')
            spend_items(user, [{'id': 44, 'num': 1}])
            data['unlocked_hero_list'].append({'hero_id': item, 'astrolabe_id_list': []})
    elif command == 66000:
        policy_rows = polyhedron_catalog()['policy']
        rows = [c for c in policy_rows if c['activity_id'] == data['activity_id']]
        if not rows:
            # 存档的 activity_id 来自多维货架（如 4407302），策略奖励表按赛季活动
            # 编号分表；没有对应赛季时使用 activity_id=0 的通用 20 级奖励。
            rows = [c for c in policy_rows if c['activity_id'] == 0]
        rows = sorted(rows, key=lambda c: c['level'])
        claimed = data['decision']['apply_id_list']
        allowed = [i for i, c in enumerate(rows, 1) if c['exp'] <= build_currency_balances(user)[45] and i not in claimed]
        indices = allowed if request['type'] == 1 else [request['level']]
        if not indices or any(i not in allowed for i in indices):
            raise ValueError('多维任务等级不足或已领奖')
        rewards = [{'id': i, 'num': n} for index in indices for i, n in rows[index-1]['rewards']]
        grant_rewards(user, rewards);claimed.extend(indices)
        user['polyhedron'] = normalize_data(data)
        return {'result': 0, 'reward_list': rewards}
    else:
        # 客户端在部分页面会先发 66038 设置连携奥义，再发 18010 开局；
        # 官服对开局前的 66038 也回 result=0。这里提前接受，避免开始挑战转圈。
        if command == 66038 and game['state'] != 2:
            progress['cooperate_unique_skill_id'] = int(
                request.get('cooperate_unique_skill_id') or 0)
            user['polyhedron'] = normalize_data(data)
            return {'result': 0}
        if game['state'] != 2:
            raise ValueError('多维路线尚未开始')
        stage = progress['stage']
        if command == 18020:
            # 官服 recruit 场景：cs_18020(hero_id) 选定一名 NPC，
            # sc_18021 后状态变为 save_point=2、reward 只包含该角色与 enlist_type。
            event = next((row for row in polyhedron_catalog()['events']
                          if row['id'] == progress['event'].get('id')), None)
            if event is None or int(event['event_type']) != 2:
                raise ValueError('当前不是招募关卡')
            if int(stage.get('save_point') or 0) != 1:
                raise ValueError('招募候选已选择或关卡尚未加载')
            hero_id = int(request.get('hero_id') or 0)
            params = [int(x) for x in stage.get('params') or []]
            pairs = list(zip(params[0::2], params[1::2]))
            selected = next(((h, p) for h, p in pairs if h == hero_id), None)
            if selected is None:
                raise ValueError('招募候选不存在')
            stage['reward'] = {'round': 1, 'item_list': [
                {'class': 3, 'params': [int(selected[0]), int(selected[1])]}]}
            stage['save_point'] = 2
        elif command == 18012:
            choices = stage['reward']['item_list']
            index = request['index']
            if stage['save_point'] != 2 or not 0 <= index <= len(choices):
                raise ValueError('多维奖励选项无效')
            if index:
                apply_item(data, choices[index-1])
            else:
                giveup = max(0, effect_value(data, 28))
                if giveup:
                    coin_change(progress, giveup)
            stage['reward']['item_list'] = []
            event = next((row for row in polyhedron_catalog()['events']
                          if row['id'] == progress['event'].get('id')), None)
            if event is None:
                raise ValueError('多维事件配置不存在')
            event_type = int(event['event_type'])
            event_id = int(event['id'])
            round_no = int(stage['reward'].get('round') or 1)
            if event_type == 6:
                # 官服 1-7 BOSS 奖励顺序实测：普通珍宝 → 医疗包 → 普通珍宝 → 专属珍宝。
                boss_categories = [int(x) for x in (stage.get('params') or [])]
                if round_no == 1:
                    stage['reward'] = {'round': 2, 'item_list': [
                        {'class': 4, 'params': [BOSS_MEDICAL_EFFECT]}]}
                elif round_no == 2:
                    first = boss_categories[0] if boss_categories else 0
                    fresh, category = _boss_normal_choices(data, first)
                    stage['params'] = [first, int(category)]
                    stage['reward'] = {'round': 3, 'item_list': fresh}
                elif round_no == 3:
                    fresh = _artifact_choices(data, 3007, exclusive=True)
                    if not fresh:
                        fresh = _artifact_choices(data, 0, exclusive=False)
                    if fresh:
                        stage['reward'] = {'round': 4, 'item_list': fresh}
                    else:
                        open_gates(data)
                        if game['state'] == 3:
                            polyhedron_end(user, data, True)
                else:
                    open_gates(data)
                    if game['state'] == 3:
                        polyhedron_end(user, data, True)
            elif event_type == 8 and round_no == 1 and effect_value(data, 31) > 0:
                # 信标「灵光一闪」：升级珍宝门额外获得一次奖励选择轮次。
                fresh = _artifact_choices(data, 0, upgrade=True)
                if fresh:
                    stage['reward'] = {'round': 2, 'item_list': fresh}
                else:
                    open_gates(data)
                    if game['state'] == 3:
                        polyhedron_end(user, data, True)
            elif event_type == 3002 and round_no == 1:
                # 精英战斗有两轮珍宝结算：第一轮选完后再发一轮同分类珍宝，
                # 不能直接开门，否则 1-6 这类精英节点只出现图二一个三选一。
                category = int(progress['event'].get('reward_type') or 0)
                fresh = _artifact_choices(data, category)
                if fresh:
                    stage['reward'] = {'round': 2, 'item_list': fresh}
                else:
                    extra = event_id // 1000 in (10001, 10002)
                    open_gates(data, progress['tier_id'] if extra else None)
                    if game['state'] == 3:
                        polyhedron_end(user, data, True)
            else:
                if event_type not in SPECIAL_GATE_TYPES or not _restore_special_gate(data, user, True):
                    extra = event_id // 1000 in (10001, 10002)
                    # 1-1 的 9001/9002 整备奖励选完后要推进到 1-2 的门；不能传当前层，
                    # 否则会继续生成 1-1 门并让首战仍显示 1-1。
                    open_gates(data, progress['tier_id'] if extra else None)
                if game['state'] == 3:
                    polyhedron_end(user, data, True)
        elif command == 18030:
            # 客户端 QueryResetRewardPolyhedron → cs_18030 / sc_18031：
            # 珍宝三选一的重置，不重新生成门、不重算战斗层数。
            if stage['save_point'] != 2:
                raise ValueError('多维当前不处于奖励保存点')
            choices = stage['reward']['item_list']
            attrs = {int(r['id']): int(r.get('value') or 0)
                     for r in progress.get('attribute_list') or []}
            if attrs.get(8, 0) <= 0:
                raise ValueError('珍宝奖励重置次数不足')
            event = next((row for row in polyhedron_catalog()['events']
                          if row['id'] == progress['event']['id']), None)
            if event is None:
                raise ValueError('多维事件配置不存在')
            event_type = int(event['event_type'])
            event_id = int(event['id'])
            round_no = int(stage['reward'].get('round') or 1)
            expected_class = 5 if event_type == 8 else 6
            if not choices or any(int(c.get('class') or 0) != expected_class for c in choices):
                raise ValueError('当前奖励不可重置')
            category = int(progress['event'].get('reward_type') or 0)
            if event_type == 6:
                boss_categories = [int(x) for x in (stage.get('params') or [])]
                if round_no == 1:
                    cat = boss_categories[0] if boss_categories else 0
                    fresh = _artifact_choices(data, cat, exclusive=False)
                elif round_no == 3:
                    cat = boss_categories[1] if len(boss_categories) > 1 else 0
                    fresh = _artifact_choices(data, cat, exclusive=False)
                elif round_no == 4:
                    fresh = _artifact_choices(data, 3007, exclusive=True)
                    if not fresh:
                        fresh = _artifact_choices(data, 0, exclusive=False)
                else:
                    raise ValueError('当前奖励不可重置')
            elif event_type == 9 and event_id == 9001:
                # 默认首关奖励刷新时必须保持原来抽到的大类，不能变成全分类随机。
                cat = int((stage.get('params') or [0])[0] or 0)
                fresh = _artifact_choices(data, cat, exclusive=False)
                if not fresh:
                    fresh, _ = _boss_normal_choices(data)
            elif event_type == 9 and event_id == 9002:
                fresh = _artifact_choices(data, 3007, exclusive=True)
                if not fresh and (stage.get('params') or []):
                    # 队长没有专属池时，9002 已兜底成某个普通大类；刷新保持该大类。
                    fresh = _artifact_choices(data, int((stage.get('params') or [0])[0] or 0),
                                              exclusive=False)
            elif event_type == 8:
                # 升级门的 gate reward_type=7 是门面图标/说明分类，不是珍宝 sub_type；
                # 实际升级池是全部已拥有且未满级的珍宝。
                fresh = _artifact_choices(data, 0, upgrade=True)
            elif event_type in (1, 3, 3002, 9, 10001, 10002):
                fresh = _artifact_choices(data, category)
            else:
                raise ValueError('当前奖励不可重置')
            if not fresh:
                raise ValueError('没有可重置的珍宝候选')
            stage['reward']['item_list'] = fresh
            attrs[8] = attrs.get(8, 0) - 1
            progress['attribute_list'] = [{'id': i, 'value': attrs.get(i, 0)}
                                           for i in range(1, 35)]
            # 刷新后仍是奖励保存点，等待 18012；不能进入选门。
            stage['save_point'] = 2
        elif command == 18014:
            if stage['save_point'] != 3:
                raise ValueError('多维当前不处于选门保存点')
            selected = next((g['event'] for g in stage['gate_list'] if g['index'] == request['index']), None)
            if not selected:
                raise ValueError('多维门不存在')
            tiers = polyhedron_catalog()['tiers']
            target_tier = int(stage['params'][0]) if stage.get('params') else 0
            special_gate = _event_type_of_id(int(selected['id'])) in SPECIAL_GATE_TYPES
            if special_gate:
                # 异常节点不算关卡层数：进入时保留当前节点，记下原来的门列表，
                # 清完特殊关后原样回到当前节点，不能把标题推进到下一关。
                user[SPECIAL_RETURN_KEY] = {
                    'tier_id': int(progress['tier_id']),
                    'event': deepcopy(progress['event']),
                    'stage': deepcopy(stage),
                    'gate_index': int(request['index']),
                }
            elif target_tier:
                if not any(t['id'] == target_tier for t in tiers):
                    raise ValueError('多维门目标层无效')
                progress['tier_id'] = target_tier
            else:
                # 兼容没有目标层字段的旧存档。
                index = next(i for i, t in enumerate(tiers) if t['id'] == progress['tier_id'])
                initial_gate = int(progress.get('event', {}).get('id') or 0) in (9001, 9002)
                if not initial_gate:
                    progress['tier_id'] = tiers[index+1]['id']
            open_stage(data, selected)
        elif command in (18022, 18024, 18026):
            from .polyhedron_shop import shop_request
            shop_request(data, command, request)
        elif command == 18028:
            heroes = request['fight_id_list']
            leader = game['start_info']['leader']['hero_id']
            owned = {h['hero_id'] for h in progress['hero_list'] if h['health']}
            if not heroes or len(heroes) > 3 or heroes[0] != leader or len(set(heroes)) != len(heroes) or not set(heroes) <= owned:
                raise ValueError('多维出战队伍无效')
            progress['fight_hero_id_list'] = list(heroes)
        elif command == 66038:
            progress['cooperate_unique_skill_id'] = request['cooperate_unique_skill_id']
        else:
            raise ValueError('当前多维节点没有对应操作')
    user['polyhedron'] = normalize_data(data)
    return {'result': 0}
