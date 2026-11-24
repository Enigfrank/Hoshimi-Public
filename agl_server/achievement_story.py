"""成就物语阅读状态：cs_53006 校验点数并记录已读。"""

from functools import lru_cache

from .currency_data import build_currency_balances
from .gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def story_catalog() -> dict[int, dict]:
    """读取客户端成就物语配置，故事 ID、名称和解锁点数以安装包为准。"""
    rows = read_client_data('''(function()
        local cfg=require('AchievementStoryCfg')
        local result={}
        for _,id in ipairs(cfg.all) do
            local row=cfg[id]
            result[#result+1]={id=row.id,name=row.name,unlock_point=row.unlock_point}
        end
        return result
    end)()''')
    return {int(row['id']): {'id': int(row['id']), 'name': row.get('name') or '',
                             'unlock_point': int(row.get('unlock_point') or 0)}
            for row in rows}


def login_story_line(user: dict) -> list[int]:
    """把存档里的已读故事 ID 规整成 sc_53001.story_line（repeated uint32）。"""
    raw = user.get('achievement_story_read')
    if not isinstance(raw, list):
        return []
    ids: set[int] = set()
    for value in raw:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(ids)


def read_achievement_story(user: dict, story_id: int) -> dict:
    """校验成就点数并写入已读列表；不满足时返回失败，不修改存档。"""
    story_id = int(story_id)
    story = story_catalog().get(story_id)
    if not story:
        raise ValueError(f'无效的成就物语 ID：{story_id}')
    points = build_currency_balances(user).get(33, 0)
    if points < story['unlock_point']:
        raise ValueError(f'成就点数未达标：当前 {points}，需要 {story["unlock_point"]}')
    read_list = user.get('achievement_story_read')
    if not isinstance(read_list, list):
        read_list = []
        user['achievement_story_read'] = read_list
    if story_id not in read_list:
        read_list.append(story_id)
        read_list.sort()
    return {'result': 0}
