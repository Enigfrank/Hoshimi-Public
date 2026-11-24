"""成就物语的解锁条件与已读状态。"""

from functools import lru_cache

from .currency_data import build_currency_balances
from .gameplay_protocol import read_client_data


@lru_cache(maxsize=1)
def story_catalog() -> dict[int, int]:
    """从客户端配置读取故事 ID 与解锁所需的成就点数。"""
    rows = read_client_data("""(function()
        local cfg=require('AchievementStoryCfg')
        local result={}
        for _,id in ipairs(cfg.all) do result[tostring(id)]=cfg[id].unlock_point end
        return result
    end)()""")
    return {int(story_id): points for story_id, points in rows.items()}


def login_story_line(user: dict) -> list[int]:
    """读取存档中的有效故事编号，去除重复值。"""
    raw = user.get('achievement_story_read')
    if not isinstance(raw, list):
        return []
    return sorted({value for value in raw if type(value) is int and 0 < value < 1 << 32})


def read_achievement_story(user: dict, story_id: int) -> None:
    """验证故事和成就点数，记录已读且不扣除点数。"""
    points = story_catalog().get(story_id)
    if points is None:
        raise ValueError('无效的成就物语 ID')
    if build_currency_balances(user).get(33, 0) < points:
        raise ValueError('成就点数未达标')
    user['achievement_story_read'] = sorted(set(login_story_line(user)) | {story_id})
