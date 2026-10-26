"""誓约系统：誓约解锁、誓约任务、婚纱照、改名与纪念日的事实存档。

客户端协议语义以 ``OathCollectionContentAction`` 为准，四个请求的实际职责是：

- ``14510 -> 14511``：只登记誓约剧情已读。建立誓约由 ``14514`` 完成，
  等级只由誓约任务推进，因此这里不写 ``oath_time`` 与 ``time_list``。
- ``14512 -> 14513``：批量提交已完成的誓约任务。``reward_list`` 恒为空：
  誓约任务本身不携带物品奖励，``1284`` 的戒指与表情改由心链剧情首读发放。
- ``14514 -> 14515``：报文不带 ``picture_url`` 时校验条件并建立誓约，返回
  ``oath_time``；带 ``picture_url`` 时只更新已有誓约的婚纱照，不返回 ``time``。
- ``14516 -> 14517``：改名，成功后把当前时间追加到 ``time_list``；客户端用
  ``time_list`` 最后一条加 ``wedding_rename_time`` 判断改名冷却。

``14518`` 在当前客户端 ``p14_pb`` 中没有 ``sc_14519``，因此不参与分发。
存档只保存协议真正下发的字段，等级与冷却一律现算，不落盘。
"""

from functools import lru_cache
import time

from backhome_catalog import home_config
from gameplay_protocol import read_client_data
from hero_oath_task import task_progress_value

# 誓约相关 CS 指令：14510 读剧情、14512 提交任务、14514 建立誓约或换婚纱照、14516 改名。
# 14518 没有对应的 SC 协议，无法确认真实语义，不纳入分发以免凭空回包。
OATH_COMMANDS = {14510, 14512, 14514, 14516}
# 一天秒数，用于把 ``wedding_rename_time`` 的天数换算为改名冷却。
DAY_SECONDS = 86400


def _list(value) -> list:
    """把 Lua 读回的空表统一成空列表，避免同一份配置出现两种空值。"""
    return list(value) if isinstance(value, list) else []


@lru_cache(maxsize=1)
def oath_catalog() -> dict:
    """读取誓约配置、誓约任务、改名冷却与等级上限，保持配置中的真实编号。"""
    catalog = read_client_data('''(function()
        local cfg=require('WeddingCfg');local rows={}
        for _,id in ipairs(cfg.all or {}) do
            local v=cfg[id]
            local plot={}
            for _,text in ipairs(v.wedding_plot or {}) do
                if text then plot[#plot+1]=text end
            end
            rows[tostring(id)]={
                id=id, ring_id=v.ring_id or 0, skin_id=v.skin_id or 0,
                read_story_id=v.read_story_id or 0, plot=plot,
                condition=v.condition or {}, plot_condition=v.plot_condition or {},
                -- read_reward 是 [物品, 数量] 二元组列表，读心链剧情首读时一次性发放。
                read_reward=v.read_reward or {}}
        end
        -- 誓约任务：wedding_level 决定任务所属阶段，need 为该阶段全部完成的门槛。
        local assign=require('WeddingAssignmentCfg');local tasks={}
        for _,tid in ipairs(assign.all or {}) do
            local t=assign[tid]
            tasks[#tasks+1]={id=tid, hero_id=t.hero_id, level=t.wedding_level or 0,
                condition=t.condition or 0, need=t.need or 1,
                additional_parameter=t.additional_parameter or {}}
        end
        local gs=require('GameSetting')
        return {rows=rows, tasks=tasks,
            rename_days=gs.wedding_rename_time.value[1],
            max_level=require('OathConst').OATH_MAX_LV}
    end)()''')
    # 空 Lua 表会被 JSON 读成空字典，这里统一成列表，保持后续判断只有一种空值。
    for row in catalog['rows'].values():
        row['condition'] = _list(row.get('condition'))
        row['plot_condition'] = _list(row.get('plot_condition'))
        row['plot'] = _list(row.get('plot'))
        row['read_reward'] = _list(row.get('read_reward'))
    for task in catalog['tasks']:
        task['additional_parameter'] = _list(task.get('additional_parameter'))
    catalog['tasks'].sort(key=lambda row: row['id'])
    return catalog


def oath_row(oath_id: int) -> dict:
    """读取存在的誓约配置，错误编号直接拒绝。"""
    row = oath_catalog()['rows'].get(str(oath_id))
    if row is None:
        raise ValueError('誓约不存在：' + str(oath_id))
    return row


def oath_state(user: dict, oath_id: int) -> dict:
    """读取单个誓约的存档事实，查询本身不修改账号。

    只返回存档真实存在的字段：``oath``、``plot``、``oath_time``、
    ``picture_link``、``nick`` 与 ``time_list``。等级、冷却和纪念记录都是派生值。
    """
    saved = (user.get('oaths') or {}).get(str(oath_id)) or {}
    return {'oath': bool(saved.get('oath', False)),
            'nick': saved.get('nick', ''),
            'picture_link': saved.get('picture_link', ''),
            'plot': sorted(set(saved.get('plot', []))),
            'time_list': sorted(saved.get('time_list', [])),
            'oath_time': saved.get('oath_time', 0)}


def saved_tasks(user: dict, oath_id: int) -> dict:
    """读取誓约任务提交标志，未提交过时返回空字典，不修改账号。"""
    saved = (user.get('oaths') or {}).get(str(oath_id)) or {}
    return saved.get('tasks') or {}


def oath_tasks(oath_id: int) -> list[dict]:
    """按配置顺序返回该誓约的全部任务。"""
    return [task for task in oath_catalog()['tasks'] if task['hero_id'] == oath_id]


def task_progress(user: dict, task: dict) -> int:
    """返回单个誓约任务由存档事实算出的当前进度，不含提交标志。"""
    return task_progress_value(user, task)


def task_saved(user: dict, task: dict) -> dict:
    """返回单个誓约任务的提交标志，未提交过时为零。"""
    saved = saved_tasks(user, task['hero_id']).get(str(task['id'])) or {}
    return {'complete_flag': int(saved.get('complete_flag', 0))}


def oath_level(user: dict, oath_id: int) -> int:
    """返回誓约等级：未建立为 0，建立后每完成一阶段全部任务升一级。

    ``WeddingAssignmentCfg`` 给每个任务标注 ``wedding_level``，客户端
    ``OathLvInfo`` 通过 ``GetOathAssignmentHeroLevelProgress`` 用已完成任务推进
    当前阶段进度条，阶段全部完成才升到下一级，上限为 ``OATH_MAX_LV``。
    誓约建立即为 1 级，因为誓约任务配置只提供 ``wedding_level`` 为 1 与 2
    的阶段，而剧情条件 ``115005``、``115006`` 分别要求誓约达到 2、3 级，
    上限 ``OATH_MAX_LV`` 为 3，恰好是"建立即 1 级 + 两阶段各升 1 级"。
    阶段必须从 1 起连续达成，因此一级未完成时二级全完成仍只算 1 级。
    """
    if not oath_state(user, oath_id)['oath']:
        return 0
    total, finished = {}, {}
    for task in oath_tasks(oath_id):
        level = int(task['level'])
        total[level] = total.get(level, 0) + 1
        if task_saved(user, task)['complete_flag']:
            finished[level] = finished.get(level, 0) + 1
    # 誓约建立即为 1 级，之后每完成一个阶段的全部任务升 1 级。
    level = 1
    while total.get(level) and finished.get(level, 0) >= total[level]:
        level += 1
    return min(level, oath_catalog()['max_level'])


def assignment_info(user: dict, oath_id: int) -> dict:
    """返回单个誓约的任务进度，字段与客户端 ``assignment_info`` 一致。

    ``progress`` 是该任务由存档事实算出的实际进度，客户端用它显示
    ``1/3`` 之类的分段进度，因此必须下发真实进度而不是提交标志；
    ``complete_flag`` 单独表示是否已提交。
    """
    rows = []
    for task in oath_tasks(oath_id):
        saved = task_saved(user, task)
        rows.append({'id': task['id'],
                     'progress': task['need'] if saved['complete_flag'] else min(
                         task['need'], task_progress(user, task)),
                     'complete_flag': saved['complete_flag'],
                     'expired_timestamp': 0})
    return {'id': oath_id, 'progress': rows}


def assignment_data(user: dict) -> dict:
    """下发全部誓约任务进度，``send_type=0`` 表示操作后触发的全量刷新。

    ``sc_14503.assignment_list`` 是 ``map<hero_id, assignment_info>>`` 而不是
    repeated 字段：客户端 ``OathAssignmentAction.OnAssignment`` 用
    ``pairs(assignment_list)`` 遍历并以角色编号取值，序列化成列表会被 protobuf
    按标量校验拒绝，因此必须下发以角色编号为键的字典。
    """
    return {'assignment_list': {str(oath_id): assignment_info(user, int(oath_id))
                                for oath_id in sorted(oath_catalog()['rows'], key=int)},
            'send_type': 0}


def oath_data(user: dict) -> list[dict]:
    """登录同步全部誓约，字段名与 ``hero_oath_net_rec`` 一致。"""
    result = []
    for oath_id in sorted(oath_catalog()['rows'], key=int):
        state = oath_state(user, int(oath_id))
        result.append({'hero_id': int(oath_id), 'oath': state['oath'],
            'oath_level': oath_level(user, int(oath_id)), 'nick': state['nick'],
            'picture_link': state['picture_link'],
            'oath_plot': {'hero_id': int(oath_id), 'text_list': state['plot']},
            'time_list': state['time_list'], 'oath_time': state['oath_time']})
    return result


def oath_pushes(user: dict) -> list[tuple]:
    """登录与誓约操作后同步誓约面板。

    客户端 ``OathCollectionContentData`` 依赖 ``14501`` 初始化誓约列表，
    ``OathAssignmentData`` 依赖 ``14503`` 初始化任务进度与红点，缺任一条
    都会让誓约面板显示为空或任务不刷新，因此两条一起下发。
    """
    return [(14501, {'hero_oath': oath_data(user)}),
            (14503, assignment_data(user))]


def assignment_pushes(user: dict) -> list[tuple]:
    """只推送誓约任务进度，供单独刷新任务列表的调用方使用。"""
    return [(14503, assignment_data(user))]


def oath_request(user: dict, command: int, request: dict, has_picture: bool = False) -> dict:
    """处理誓约剧情、任务提交、建立誓约、婚纱照与改名，返回对应 SC 字段。

    ``has_picture`` 来自原始 protobuf 的字段存在性：``14514`` 复用同一指令
    实现"建立誓约"和"上传婚纱照"，而 ``decode_message`` 会把未携带的
    optional 字符串解成空串，无法据此区分。

    拒绝时抛出 ``ValueError``，由调用方回 ``result=1``；成功时调用方负责存档。
    """
    if command == 14510:
        return read_oath_plot(user, request)
    if command == 14512:
        return submit_oath_tasks(user, request)
    if command == 14514:
        return update_oath_photo(user, request, has_picture)
    if command == 14516:
        return rename_oath_hero(user, request)
    raise ValueError('未实现的誓约指令：' + str(command))


def read_story_reward(user: dict, story_id: int) -> list[dict]:
    """心链剧情首次阅读时按 ``WeddingCfg.read_reward`` 发放物品，重复阅读不再发放。

    ``read_story_id`` 与 ``read_reward`` 一一对应：只有 1284 配置了 ``933031001``
    与对应的誓约戒指、表情，其余誓约 ``read_story_id`` 为 0，配置也没有奖励表。
    奖励里的誓约戒指正是建立誓约的前置条件，因此这里不要求誓约已建立。
    是否首读由 ``read_stories`` 与 ``oath_story_reward_claims`` 双重判定：
    ``read_stories`` 覆盖历史存档已读过的剧情，领取标记覆盖奖励发放成功后
    剧情已读的情况，二者任一命中都不再发放，避免删读后重复领奖。
    必须在剧情写入已读列表之前调用，奖励发放成功后才写领取标记。
    """
    if story_id in (user.get('read_stories') or []):
        return []
    if story_id in (user.get('oath_story_reward_claims') or []):
        return []
    for oath_id in sorted(oath_catalog()['rows'], key=int):
        row = oath_catalog()['rows'][oath_id]
        if int(row['read_story_id']) != int(story_id) or not row['read_reward']:
            continue
        rewards = [{'id': int(item), 'num': int(num)} for item, num in row['read_reward']]
        from battle_rewards import grant_rewards
        grant_rewards(user, rewards)
        # 只有奖励真正发放成功才记录领取标记，发奖失败时不留痕迹。
        user.setdefault('oath_story_reward_claims', []).append(int(story_id))
        return rewards
    return []


def read_oath_plot(user: dict, request: dict) -> dict:
    """登记誓约剧情已读，按剧情在配置中的位置校验对应阶段条件。

    客户端 ``IsPlotStoryUnlocked`` 用剧情编号在 ``wedding_plot`` 中的序号取
    ``plot_condition``，因此必须按序号校验，而不是只看誓约是否建立。
    读剧情不建立誓约、不升级，也不写纪念日，这些分别由 ``14514`` 和任务提交负责。
    """
    plot = request['oath_plot']
    hero_id = plot['hero_id']
    row = oath_row(hero_id)
    texts = list(plot['text_list'])
    if not texts or len(set(texts)) != len(texts):
        raise ValueError('誓约剧情列表为空或重复')
    if not set(texts) <= set(row['plot']):
        raise ValueError('誓约剧情编号无效')
    saved = oath_state(user, hero_id)
    if set(texts) & set(saved['plot']):
        raise ValueError('誓约剧情已读')
    for text_id in texts:
        index = row['plot'].index(text_id) + 1
        if index > len(row['plot_condition']):
            raise ValueError('誓约剧情缺少阶段条件配置')
        if not oath_condition(user, row['plot_condition'][index - 1]):
            raise ValueError('誓约剧情条件未达成')
    # 全部条件通过后再落盘，失败请求不得在账号里留下空誓约结构。
    state = _writable_oath(user, hero_id)
    state['plot'] = sorted(set(state['plot']) | set(texts))
    _store_oath(user, hero_id, state)
    return {'result': 0}


def submit_oath_tasks(user: dict, request: dict) -> dict:
    """批量提交已完成且未提交过的誓约任务，誓约任务本身不发放物品。"""
    ids = list(request['id'])
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('誓约任务列表为空或重复')
    catalog = {task['id']: task for task in oath_catalog()['tasks']}
    planned = []
    for task_id in ids:
        task = catalog.get(task_id)
        if not task:
            raise ValueError('誓约任务不存在：' + str(task_id))
        oath_id = task['hero_id']
        if not oath_state(user, oath_id)['oath']:
            raise ValueError('誓约尚未建立')
        # 阶段按提交前的等级判断，同一批不能借提交一级任务跨到二级。
        if int(task['level']) > oath_level(user, oath_id):
            raise ValueError('誓约等级尚未达到该任务阶段')
        if task_saved(user, task)['complete_flag']:
            raise ValueError('誓约任务已提交')
        if task_progress(user, task) < task['need']:
            raise ValueError('誓约任务尚未完成')
        planned.append((oath_id, task_id))
    # 整批校验通过后才落盘，避免中途失败让已判定的任务丢失提交资格。
    for oath_id, task_id in planned:
        state = _writable_oath(user, oath_id)
        state['tasks'][str(task_id)] = {'complete_flag': 1}
        _store_oath(user, oath_id, state)
    # 誓约任务配置没有物品奖励：1284 的戒指与表情由心链剧情首读发放。
    return {'result': 0, 'reward_list': []}


def update_oath_photo(user: dict, request: dict, has_picture: bool = False) -> dict:
    """报文带 ``picture_url`` 只更新婚纱照，不带时建立誓约并返回誓约时间。

    ``cs_14514.picture_url`` 是 optional 标量，``decode_message`` 会把未携带
    的空字符串与"携带了空字符串"混为一谈，因此分支依据是原始报文的字段存在性
    ``has_picture``，而不是解码后的字符串是否为空。客户端
    ``OathCollectionContentAction.OnOath`` 不传地址建立誓约，
    ``OnUpdateWeddingPhoto`` 必定带地址，两个方向不会互相误判。
    """
    hero_id = request['hero_id']
    row = oath_row(hero_id)
    url = (request.get('picture_url') or '').strip()
    if url and len(url) > 128:
        raise ValueError('婚纱照地址过长')
    if has_picture:
        # 上传婚纱照只作用于已建立的誓约，且不回 time：客户端用 time 刷新誓约倒计时。
        if not oath_state(user, hero_id)['oath']:
            raise ValueError('誓约尚未建立')
        if not url:
            raise ValueError('婚纱照地址为空')
        state = _writable_oath(user, hero_id)
        state['picture_link'] = url
        _store_oath(user, hero_id, state)
        return {'result': 0}
    saved = oath_state(user, hero_id)
    if not saved['oath']:
        # 条件全部通过后再写存档，失败请求不得留下空誓约结构。
        if not oath_conditions_met(user, row['condition']):
            raise ValueError('誓约解锁条件未达成')
        state = _writable_oath(user, hero_id)
        now = int(time.time())
        state['oath'] = True
        state['oath_time'] = now
        # 建立誓约本身不写入纪念日：time_list 只记录改名，客户端据此算改名冷却。
        state['time_list'] = []
        _store_oath(user, hero_id, state)
        return {'result': 0, 'time': now}
    # 誓约已建立时只回誓约时间，不改写婚纱照与纪念日。
    return {'result': 0, 'time': saved['oath_time']}


def rename_oath_hero(user: dict, request: dict) -> dict:
    """改名誓约昵称，成功后追加一条纪念日并进入按配置天数计算的冷却。"""
    hero_id = request['hero_id']
    oath_row(hero_id)
    if not oath_state(user, hero_id)['oath']:
        raise ValueError('誓约尚未建立')
    nick = (request.get('nick') or '').strip()
    if len(nick) > 12:
        raise ValueError('誓约昵称长度不得超过 12')
    if nick and nick == user.get('nick'):
        raise ValueError('誓约昵称已被使用')
    if rename_available_at(user, hero_id) > int(time.time()):
        raise ValueError('誓约改名仍在冷却中')
    now = int(time.time())
    state = _writable_oath(user, hero_id)
    state['nick'] = nick
    state['time_list'] = sorted(state['time_list'] + [now])
    _store_oath(user, hero_id, state)
    return {'result': 0}


def rename_available_at(user: dict, oath_id: int) -> int:
    """返回下一次可改名的时刻，与客户端 ``CheckNickNameCanRename`` 一致。

    客户端以 ``time_list`` 最后一条加 ``wedding_rename_time`` 天判断冷却，
    因此服务端不额外保存 ``rename_time``，避免与客户端显示不一致。
    """
    marks = oath_state(user, oath_id)['time_list']
    if not marks:
        return 0
    return marks[-1] + oath_catalog()['rename_days'] * DAY_SECONDS


def _writable_oath(user: dict, oath_id: int) -> dict:
    """取出单个誓约的可写存档，保留已有事实并补齐缺省字段。"""
    saved = user.setdefault('oaths', {}).get(str(oath_id))
    if saved is None:
        return {'oath': False, 'plot': [], 'oath_time': 0, 'picture_link': '', 'nick': '',
                'time_list': [], 'tasks': {}}
    saved.setdefault('plot', [])
    saved.setdefault('time_list', [])
    saved.setdefault('tasks', {})
    return saved


def _store_oath(user: dict, oath_id: int, state: dict) -> None:
    """把誓约事实写回存档，只保留协议真正下发的字段与任务提交标志。"""
    user.setdefault('oaths', {})[str(oath_id)] = {
        'oath': bool(state.get('oath', False)),
        'plot': sorted(set(state.get('plot', []))),
        'oath_time': int(state.get('oath_time', 0)),
        'picture_link': state.get('picture_link', ''),
        'nick': state.get('nick', ''),
        'time_list': sorted(state.get('time_list', [])),
        'tasks': {str(task_id): {'complete_flag': int(row.get('complete_flag', 0))}
                  for task_id, row in (state.get('tasks') or {}).items()}}


def oath_condition(user: dict, condition: int) -> bool:
    """判定誓约配置引用的条件，类型以客户端 ``ConditionCfg`` 为准。

    誓约只引用五种类型：

    - ``330000(hero)``：与该角色已经建立誓约；
    - ``330001(item, count)``：持有指定誓约戒指的数量；
    - ``330002(hero, level)``：该誓约等级达到 N；
    - ``330003(hero)``：拥有该角色，配置描述为"拥有对应修正者"；
    - ``1027(hero, level)``：共享档案好感达到 N 级；
    - ``1028(hero, value)``：角色 ``trust_level`` 达到 N，描述为"交心值达到至交"。
    """
    if not condition:
        return True
    cfg = home_config('ConditionCfg', condition)
    kind, args = cfg['type'], cfg['params']
    if kind == 330000:
        return oath_state(user, args[0])['oath']
    if kind == 330001:
        return ring_holding(user, args[0]) >= args[1]
    if kind == 330002:
        return oath_level(user, args[0]) >= args[1]
    if kind == 330003:
        return bool(owned_hero(user, args[0]))
    if kind == 1027:
        return love_level(user, args[0]) >= args[1]
    if kind == 1028:
        hero = owned_hero(user, args[0])
        return bool(hero) and int(hero.get('trust_level', 1)) >= args[1]
    return False


def oath_conditions_met(user: dict, conditions: list) -> bool:
    """按配置顺序判定誓约的全部条件，任一不满足即不成立。"""
    return all(oath_condition(user, item) for item in conditions)


def owned_hero(user: dict, hero_id: int) -> dict:
    """返回已拥有的角色存档，未拥有时返回空字典。"""
    return next((h for h in user.get('heroes', [])
                 if h.get('id') == hero_id and h.get('unlock', 1)), {})


def love_level(user: dict, hero_id: int) -> int:
    """按共享档案好感经验换算好感等级，与客户端逐级经验表一致。

    ``GameDisplayCfg.hero_love_level_max`` 为 5 级，各级经验为 100/200/300/400，
    因此 0/100/300/600/1000 依次对应 1 到 5 级。满级那一档的经验为 0，
    只作等级上限声明，不能当成一次升级步骤，否则满好感会被算成 6 级。
    """
    from hero_affinity import archive_data
    from hero_training_data import training_catalog
    catalog = training_catalog()
    archive = next((row for row in archive_data(user)
                    if hero_id in catalog['HeroRecordCfg'][str(row['archive_id'])]['hero_id']), None)
    if archive is None:
        return 0
    maximum = catalog['settings']['love_level_max']
    remaining, level = int(archive['exp']), 1
    # 只遍历真正消耗经验的升级档，最后一档是满级声明。
    for step in range(1, maximum):
        need = catalog['GameLevelSetting'][str(step)]['hero_love_exp']
        if remaining < need:
            break
        remaining -= need
        level += 1
    return min(level, maximum)


def ring_holding(user: dict, ring_id: int) -> int:
    """读取誓约戒指的持有数量，誓约之戒与最初的誓约都按材料结算。"""
    return int(user.get('materials', {}).get(str(ring_id), 0))
